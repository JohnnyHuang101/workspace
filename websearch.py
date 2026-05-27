function mergeBranchToTrunk(dag: SessionDAG, branchTipId: string) {
    // 1. Traverse backward to isolate just the branch tokens
    const branchNodes = walkUpToTrunk(dag, branchTipId);

    // 2. Inject the "Glue" so the LLM doesn't hallucinate a time-jump
    const transitionMsg = {
        id: crypto.randomUUID(),
        parentId: dag.trunkLeafId,
        role: 'system',
        content: "SYSTEM: The user explored an alternative path. Findings are appended below."
    };
    dag.messages.set(transitionMsg.id, transitionMsg);
    
    // 3. The Rebase: Clone the branch nodes so they append to the new transition
    let currentParent = transitionMsg.id;
    for (const msg of branchNodes) {
        // Strip the "isSideBranch" flag so it becomes permanent history
        const { isSideBranch, ...cleanMsg } = msg.metadata || {};
        
        const clonedMsg = { 
            ...msg, 
            id: crypto.randomUUID(), 
            parentId: currentParent, // Point to the new timeline
            metadata: cleanMsg
        };
        
        dag.messages.set(clonedMsg.id, clonedMsg);
        appendToJsonlTape(clonedMsg); // Write the flattened timeline to Linux disk
        
        currentParent = clonedMsg.id;
    }

    // 4. Update the pointers to finalize the merge
    dag.trunkLeafId = currentParent;
    dag.activeLeafId = dag.trunkLeafId;
}// The user asks a side question. The trunkLeafId stays frozen.
const sideBranchNode = {
    id: crypto.randomUUID(),
    parentId: dag.trunkLeafId, // Points to the main history
    role: 'user',
    content: "/btw check if this alternative URL has a cheaper price",
    metadata: { isSideBranch: true } // Prevents context pollution
};class SessionDAG {
    public messages = new Map<string, Message>();
    
    // The exact point where the main history stops
    public trunkLeafId: string | null = null; 
    
    // Where the user's current branch is looking
    public activeLeafId: string | null = null; 
}
