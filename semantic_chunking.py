import multiprocessing
from multiprocessing import Process, Queue as MPQueue
import os
import queue
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import psutil
import pypdf
import torch
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
import re

# ── Tunable constants ──────────────────────────────────────────────────────────
EMBED_BATCH_SIZE  = 1024
UPSERT_BATCH_SIZE = 512
NLP_WORKERS       = 15
CHUNK_WORKERS     = 1
COSINE_THRESHOLD  = 0.4
COLLECTION_NAME   = "ai_knowledge"
MONITOR_INTERVAL  = 5.0
MAX_TOKENS        = 512

_t0 = time.perf_counter()

def trace(stage: str, msg: str) -> None:
    elapsed = time.perf_counter() - _t0
    thread  = threading.current_thread().name
    print(f"[{elapsed:>8.3f}s] [{thread:<20}] [{stage:<12}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 0 — Monitor
# ─────────────────────────────────────────────────────────────────────────────
def _monitor_worker(sent_q, embed_out_q, upsert_q, stop_event, interval=MONITOR_INTERVAL):
    proc     = psutil.Process(os.getpid())
    has_cuda = torch.cuda.is_available()

    while not stop_event.is_set():
        # MPQueue.qsize() raises NotImplementedError on macOS — guard it
        try:
            sq = sent_q.qsize()
        except NotImplementedError:
            sq = -1
        try:
            eq = embed_out_q.qsize()
        except NotImplementedError:
            eq = -1

      

        ram_gb = proc.memory_info().rss / 1024**3
        print(
            f"[MON] sent_q={sq}  embed_q={eq}",
            flush=True,
        )

        stop_event.wait(interval)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — NLP Workers (UPGRADED TO MULTIPROCESSING)
# ─────────────────────────────────────────────────────────────────────────────
# ── globals set by the pool initializer ────────────────────────────────────
_process_global_nlp         = None
_process_global_sent_q      = None   # ← add these two
_process_global_error_event = None   # ←


def _nlp_initializer(model_name: str, sent_q, error_event) -> None:
    """Called once per child process when the pool starts."""
    import spacy
    global _process_global_nlp, _process_global_sent_q, _process_global_error_event

    nlp = spacy.blank("en")
    nlp.add_pipe("sentencizer")
    _process_global_nlp         = nlp
    _process_global_sent_q      = sent_q        # ← store
    _process_global_error_event = error_event   # ← store


def _should_keep(text: str) -> bool:
    """Return False for noise sentences not worth embedding."""
    stripped = text.strip()
    
    # Too short to be meaningful
    if len(stripped) < 20:
        return False
    
    # Count real words (letters only, length > 1)
    words = stripped.split()
    real_words = [w for w in words if re.search(r'[a-zA-Z]{2,}', w)]
    
    # Less than 40% real words = math/number soup
    if len(words) > 0 and len(real_words) / len(words) < 0.4:
        return False
    
    # Bibliography / citation patterns
    citation_patterns = [
        r'^\s*[A-Z][a-z]+,\s+[A-Z][\.,]',          # "Greig, D., B. Porteous"
        r'(MIT|Cambridge|Oxford|Springer)\s+Press',   # publisher lines
        r'^\s*\d{4}[;,\.]',                          # starts with year "1989;"
        r'et al\.,?\s*$',                             # ends with "et al.,"
        r'^\s*[A-Z][a-z]+\s+et al',                  # "LeCun et al"
        r'arXiv:\d+\.\d+',                            # arxiv refs
        r'doi:\s*10\.\d+',                            # DOI lines
        r'^\s*[\d\s\.\,\;\:]+$',                      # pure numbers/punctuation
    ]
    for pat in citation_patterns:
        if re.search(pat, stripped):
            return False
    
    # Heavy math: lots of { } ^ _ = symbols relative to text length
    math_chars = sum(stripped.count(c) for c in '{}^_=∑∫∂∇')
    if math_chars > len(stripped) * 0.05:  # >5% math symbols
        return False
    
    # Inline equation fragments like "= N 2 ln β − N 2 ln(2π)"
    # (real_words ratio catches most of these already)
    
    return True

def _nlp_worker(file_path, page_num, raw) -> None:   # ← no more sent_q / error_event args
    global _process_global_nlp, _process_global_sent_q, _process_global_error_event
    sent_q      = _process_global_sent_q
    error_event = _process_global_error_event

    fname = os.path.basename(file_path)
    # trace("NLP", f"START {fname} p{page_num} ({len(raw)} chars)")

    if error_event.is_set():
        trace("NLP", f"SKIP {fname} p{page_num} — error_event set")
        return
    if not raw.strip():
        # trace("NLP", f"SKIP {fname} p{page_num} — empty")
        return

    nlp = _process_global_nlp
    t0  = time.perf_counter()
    doc = nlp(raw)
    # trace("NLP", f"spaCy took {(time.perf_counter()-t0)*1000:.1f}ms")

    pos = 0
    for sent in doc.sents:
        text = sent.text.strip()
        if not text:
            continue
        if not _should_keep(text): 
            continue
        tokens = text.split()
        chunks = (
            [" ".join(tokens[i:i + MAX_TOKENS]) for i in range(0, len(tokens), MAX_TOKENS)]
            if len(tokens) > MAX_TOKENS else [text]
        )
        for chunk in chunks:
            sent_q.put({"doc_id": fname, "page": page_num + 1, "pos": pos, "text": chunk})
            pos += 1

    # trace("NLP", f"DONE {fname} p{page_num} — {pos} sentences in {time.perf_counter()-t0:.3f}s")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Embedder  (runs in a child process — separate GIL)
# ─────────────────────────────────────────────────────────────────────────────
def _embedder_worker(embedder, sent_q, embed_out_q, n_chunk_workers, error_event) -> None:
    trace("EMBEDDER", "Started — waiting for sentences")
    pending = []

    def _flush(items):
        trace("EMBEDDER", f"Flushing {len(items)} sentences to GPU")
        texts = [it["text"] for it in items]
        with torch.inference_mode():
            vecs = embedder.encode(
                texts,
                batch_size=EMBED_BATCH_SIZE, # 500 sentences, usualyl from 1 pdf but may be from 2 pdfs
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        groups = defaultdict(list)
        for item, vec in zip(items, vecs):
            groups[item["doc_id"]].append({**item, "vector": vec})
        for doc_items in groups.values():
            doc_items.sort(key=lambda x: x["pos"]) # for eahc group ie each document_id, put into embed_out_q. items in there are segmetns of varying sentences ie 10,15,22,15,21
            embed_out_q.put(doc_items)

    try:
        while True:
            try:
                item = sent_q.get(timeout=2.0)
            except queue.Empty:
                trace("EMBEDDER", f"QUEUE IDLE SOMETHING IS WRONG WIHT NLP NOT GIVING ME DATA")
                # if pending:
                #     _flush(pending)
                #     pending = []
                continue

            if item is None:
                trace("EMBEDDER", f"Got sentinel — flushing {len(pending)} remaining")
                if pending:
                    _flush(pending)
                break

            pending.append(item)
            # trace("EMBEDDER", f"Got sentence #{len(pending)}")
            if len(pending) >= EMBED_BATCH_SIZE:
                _flush(pending)
                pending = []

    except Exception as e:
        trace("EMBEDDER", f"CRASHED: {type(e).__name__}: {e}")
        error_event.set()
    finally:
        for i in range(n_chunk_workers):
            trace("EMBEDDER", f"Sending sentinel {i+1}/{n_chunk_workers} to embed_out_q")
            embed_out_q.put(None)
        trace("EMBEDDER", "Done")


def _embedder_process_entry(embed_model_name, sent_q, embed_out_q, chunk_workers, error_event) -> None:
    """Entry point for the embedder child process."""
    from sentence_transformers import SentenceTransformer
    embedder = SentenceTransformer(embed_model_name, device="cuda")

    device = next(embedder._modules['0'].auto_model.parameters()).device
    trace("EMBEDDER", f"Model loaded on: {device}")
    trace("EMBEDDER", f"CUDA available: {torch.cuda.is_available()}")
    trace("EMBEDDER", f"VRAM allocated: {torch.cuda.memory_allocated()/1024**2:.1f} MB")


    _embedder_worker(embedder, sent_q, embed_out_q, chunk_workers, error_event)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Chunking Workers
# ─────────────────────────────────────────────────────────────────────────────
def _chunking_worker(embed_out_q, upsert_q) -> None:
    try:
        while True:
            doc_items = embed_out_q.get()
            if doc_items is None:
                break
            _split_and_enqueue(doc_items, upsert_q)
    except Exception as e:
        trace("CHUNKER", f"CRASHED: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()


def _split_and_enqueue(doc_items: list[dict], upsert_q) -> None:
    vecs = np.array([it["vector"] for it in doc_items], dtype=np.float32)

    if len(doc_items) == 1:
        _push_chunk(doc_items, vecs, 0, 1, upsert_q)
        return

    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    norm_vecs = vecs / norms

    cosines   = np.sum(norm_vecs[:-1] * norm_vecs[1:], axis=1)
    distances = 1.0 - cosines
    splits    = np.where(distances > COSINE_THRESHOLD)[0]

    boundaries = [0, *(int(s) + 1 for s in splits), len(doc_items)] # say we have 500 sentences. its broken up into SEMANTIC chunks, for each chunk add it to uspert queue.
    for i in range(len(boundaries) - 1):
        _push_chunk(doc_items, vecs, boundaries[i], boundaries[i + 1], upsert_q)
    
    trace("CHUNKER", f"Pushed {len(boundaries)} chunks into upsert_q.")




def _push_chunk(doc_items, vecs, start, end, upsert_q) -> None:
    segment    = doc_items[start:end]
    chunk_text = " ".join(it["text"] for it in segment)
    chunk_vec  = np.mean(vecs[start:end], axis=0)
    norm       = np.linalg.norm(chunk_vec)
    if norm > 0:
        chunk_vec /= norm

    upsert_q.put(PointStruct( # upsert_q: each item is a chunk
        id=str(uuid.uuid4()),
        vector=chunk_vec.tolist(),
        payload={
            "text":        chunk_text,
            "source_file": segment[0]["doc_id"],
            "page_n":      segment[0]["page"],
        },
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 — Qdrant Upsert Worker
# ─────────────────────────────────────────────────────────────────────────────
def _upsert_worker(vector_db: QdrantClient, upsert_q: queue.Queue) -> None:
    batch: list[PointStruct] = []
    total = 0

    def _flush() -> None:
        nonlocal total
        try:
            trace("UPSERT", f"Flushing {len(batch)} chunks")
            vector_db.upsert(collection_name=COLLECTION_NAME, wait=True, points=batch)
            total += len(batch)
            batch.clear()
        except Exception as e:
            trace("UPSERT", f"FLUSH FAILED: {type(e).__name__}: {e}")
            raise  # re-raise so the outer loop sees it

    try:
        while True:
            item = upsert_q.get()
            if item is None:
                if batch:
                    _flush()
                break
            batch.append(item)
            if len(batch) >= UPSERT_BATCH_SIZE:
                _flush()
    except Exception as e:
        trace("UPSERT", f"CRASHED: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
    finally:
        print(f"[Qdrant] Done. Total chunks upserted: {total}")


# ─────────────────────────────────────────────────────────────────────────────
# PDF Iterator
# ─────────────────────────────────────────────────────────────────────────────
def get_pdf_tasks(pdf_paths):
    for file_path in pdf_paths:
        try:
            with open(file_path, "rb") as fh:
                reader = pypdf.PdfReader(fh, strict=False)
                for page_num, page in enumerate(reader.pages):
                    text = page.extract_text() or ""
                    yield file_path, page_num, text
        except Exception as e:
            trace("ITERATOR", f"FAILED on {file_path}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main Pipeline
# ─────────────────────────────────────────────────────────────────────────────
def ingest_pdfs(
    pdf_paths: list[str],
    vector_db: QdrantClient,
    embed_model_name: str,          # e.g. "sentence-transformers/all-MiniLM-L6-v2"
    nlp_model_name: str = "en_core_web_sm",
) -> None:
    """
    Thread/process layout:
      1 monitor thread      — queue depth + memory dashboard
      NLP_WORKERS processes — pypdf + spaCy (CPU; bypasses GIL via ProcessPoolExecutor)
      1 embedder PROCESS    — GPU encode (separate GIL; never freezes NLP workers)
      CHUNK_WORKERS threads — cosine sim (CPU; numpy releases GIL)
      1 upsert thread       — Qdrant network I/O

    Queue boundaries:
      sent_q      MPQueue  — crosses process boundary (NLP workers → embedder process)
      embed_out_q MPQueue  — crosses process boundary (embedder process → chunker threads)
      upsert_q    Queue    — stays in main process   (chunker threads → upsert thread)
    """
    # Queues that cross the process boundary must be MPQueue
    sent_q      = MPQueue(maxsize=30000)
    embed_out_q = MPQueue(maxsize=50)
    # Stays in-process — regular Queue is faster
    upsert_q    = queue.Queue(maxsize=5000)
    # Must be multiprocessing.Event so child processes can read/set it
    error_event = multiprocessing.Event()

    # ── Monitor ───────────────────────────────────────────────────────────────
    stop_monitor   = threading.Event()
    monitor_thread = threading.Thread(
        target=_monitor_worker,
        args=(sent_q, embed_out_q, upsert_q, stop_monitor),
        name="monitor",
        daemon=True,
    )
    monitor_thread.start()

    # ── Downstream workers (start before NLP so they're ready to consume) ────
    chunk_threads = [
        threading.Thread(
            target=_chunking_worker,
            args=(embed_out_q, upsert_q),
            name=f"chunker-{i}",
            daemon=True,
        )
        for i in range(CHUNK_WORKERS)
    ]
    upsert_thread = threading.Thread(
        target=_upsert_worker,
        args=(vector_db, upsert_q),
        name="qdrant",
        daemon=True,
    )
    embed_proc = Process(
        target=_embedder_process_entry,
        args=(embed_model_name, sent_q, embed_out_q, CHUNK_WORKERS, error_event),
        name="embedder",
        daemon=True,
    )

    for t in chunk_threads:
        t.start()
    upsert_thread.start()
    embed_proc.start()

    # ── NLP fan-out ───────────────────────────────────────────────────────────
    trace("ORCH", f"Submitting tasks to ProcessPoolExecutor (NLP_WORKERS={NLP_WORKERS})")

    # Swapped to ProcessPoolExecutor for true parallel execution
    with ProcessPoolExecutor(
        max_workers=NLP_WORKERS,
        initializer=_nlp_initializer,
        initargs=(nlp_model_name, sent_q, error_event),  # ← pass here
    ) as pool:
        futures = {}
        for fp, pn, text in get_pdf_tasks(pdf_paths):
            if error_event.is_set():
                break
            fut = pool.submit(_nlp_worker, fp, pn, text)  # ← only plain picklable args
            futures[fut] = (fp, pn)

        failed = 0
        for fut in futures:
            try:
                fut.result()
            except Exception as e:
                failed += 1
                trace("ORCH", f"Task FAILED {futures[fut]}: {repr(e)}")

    if failed:
        trace("ORCH", f"{failed}/{len(futures)} NLP tasks failed")

    # ── Sentinel propagation ──────────────────────────────────────────────────
    sent_q.put(None)          # → embedder drains + sends CHUNK_WORKERS Nones to embed_out_q
    embed_proc.join()         # wait for embedder to finish

    for t in chunk_threads:
        t.join()              # each chunker exits on its own None

    upsert_q.put(None)        # → upsert worker flushes remainder and exits
    upsert_thread.join()

    stop_monitor.set()
    monitor_thread.join()

    print("\n✓ Ingestion complete.")


if __name__ == "__main__":
    from qdrant_client.models import VectorParams, Distance

    print("Initializing environment components...")
    qdrant_client = QdrantClient(url="http://localhost:6333")

    print("Re-creating Qdrant collection 'ai_knowledge'...")
    qdrant_client.recreate_collection(
        collection_name="ai_knowledge",
        # MiniLM-L6-v2 outputs 384 dimensions! 
        vectors_config=VectorParams(size=384, distance=Distance.COSINE)
    )

    # Safely grab only PDF files from the directory
    pdf_dir = os.path.join(os.getcwd(), "ai_pdfs")
    file_paths = [
        os.path.join(pdf_dir, f) 
        for f in os.listdir(pdf_dir) 
        if f.lower().endswith(".pdf")
    ]

    print(f"Found {len(file_paths)} PDFs to ingest. Starting pipeline...")

    ingest_pdfs(
        pdf_paths=file_paths,
        vector_db=qdrant_client,
        embed_model_name="BAAI/bge-small-en-v1.5",
        nlp_model_name="en_core_web_sm",
    )