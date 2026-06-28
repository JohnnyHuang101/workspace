import time
import threading
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import spacy

# --- Configuration ---
NUM_PAGES = 40        # Number of dummy pages to process
WORKERS = 4           # Number of concurrent workers
MODEL = "en_core_web_sm"

# Create a hefty block of text to simulate a dense PDF page
DUMMY_TEXT = (
    "Machine learning is a field of study in artificial intelligence. "
    "It involves the development of statistical algorithms that can learn from data. "
    "These models generalize to unseen data, making predictions or decisions. "
    "Deep learning, a subset of ML, utilizes neural networks with many layers. "
    "The Global Interpreter Lock (GIL) is a mutex that protects access to Python objects. "
) * 100 # Multiply to make it computationally heavy

# --- Thread-Local / Process-Global Setup ---
_thread_local = threading.local()
_process_global_nlp = None

def _thread_initializer(model_name):
    """Initializes spaCy uniquely for each thread."""
    _thread_local.nlp = spacy.load(model_name)

def _process_initializer(model_name):
    """Initializes spaCy uniquely for each process."""
    global _process_global_nlp
    _process_global_nlp = spacy.load(model_name)

# --- Workers ---
def nlp_thread_worker(page_id, text):
    """Worker for threads using thread-local storage."""
    nlp = _thread_local.nlp
    doc = nlp(text)
    # Force evaluation of the generator to simulate work
    sentences = [sent.text for sent in doc.sents]
    return len(sentences)

def nlp_process_worker(page_id, text):
    """Worker for processes using process-global storage."""
    global _process_global_nlp
    doc = _process_global_nlp(text)
    sentences = [sent.text for sent in doc.sents]
    return len(sentences)

def nlp_serial_worker(nlp, page_id, text):
    """Worker for serial execution passing the model directly."""
    doc = nlp(text)
    sentences = [sent.text for sent in doc.sents]
    return len(sentences)

# --- Test Runners ---
def run_serial(pages):
    print("\n--- Running Serial (1 Process, 1 Thread) ---")
    nlp = spacy.load(MODEL)
    start = time.perf_counter()
    
    total_sents = 0
    for i, text in enumerate(pages):
        total_sents += nlp_serial_worker(nlp, i, text)
        
    elapsed = time.perf_counter() - start
    print(f"Serial Time: {elapsed:.2f} seconds (Processed {total_sents} sentences)")
    return elapsed

def run_threads(pages):
    print(f"\n--- Running ThreadPoolExecutor ({WORKERS} Threads) ---")
    start = time.perf_counter()
    
    total_sents = 0
    with ThreadPoolExecutor(
        max_workers=WORKERS, 
        initializer=_thread_initializer, 
        initargs=(MODEL,)
    ) as pool:
        futures = [pool.submit(nlp_thread_worker, i, text) for i, text in enumerate(pages)]
        for fut in futures:
            total_sents += fut.result()
            
    elapsed = time.perf_counter() - start
    print(f"Threads Time: {elapsed:.2f} seconds (Processed {total_sents} sentences)")
    return elapsed

def run_processes(pages):
    print(f"\n--- Running ProcessPoolExecutor ({WORKERS} Processes) ---")
    start = time.perf_counter()
    
    total_sents = 0
    with ProcessPoolExecutor(
        max_workers=WORKERS, 
        initializer=_process_initializer, 
        initargs=(MODEL,)
    ) as pool:
        futures = [pool.submit(nlp_process_worker, i, text) for i, text in enumerate(pages)]
        for fut in futures:
            total_sents += fut.result()
            
    elapsed = time.perf_counter() - start
    print(f"Processes Time: {elapsed:.2f} seconds (Processed {total_sents} sentences)")
    return elapsed

if __name__ == "__main__":
    print(f"Generating {NUM_PAGES} pages of dummy text...")
    pages = [DUMMY_TEXT for _ in range(40)]
    
    # 1. Baseline
    t_serial = run_serial(pages)
    
    # 2. Threading (GIL bound)
    # t_threads = run_threads(pages)
    
    # 3. Multiprocessing (GIL bypassed)
    t_processes = run_processes(pages)
    
    print("\n" + "="*40)
    print("RESULTS SUMMARY")
    print("="*40)
    print(f"Serial (Baseline):  {t_serial:.2f}s")
    # print(f"Threads (GIL):      {t_threads:.2f}s ({(t_threads/t_serial)*100:.0f}% of serial time)")
    print(f"Processes (True):   {t_processes:.2f}s ({(t_processes/t_serial)*100:.0f}% of serial time)")
    print("="*40)