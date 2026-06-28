from typing import Dict, Any, List, TypedDict , Optional

class NormalizedListing(TypedDict):

    title: str 
    price: float 
    currency: str 
    free_shipping: bool
    condition: str 
    marketplace_url: str 
    source_type: str # image, text search


class AgentStates(TypedDict):
    initial_url: str # provided by user 

    target_product_title: str 
    target_product_description: str 
    target_image_path: str 

    execution_plan: List[Dict[str,Any]] # 1) find similar products: (function,parameters)

    raw_search_resutls: List[Dict[str, Any]]# list of query: [result url1, result url2, result url 3]

    normalized_listings: List[NormalizedListing]
    validated_listings: List[NormalizedListing] # pass throuhgn like a fuynction latermfor validating

    final_comparison_report: str 

    error_count: int 
    current_node: str 
    next_node: Optional[str]

    