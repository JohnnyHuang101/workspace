# test.py
import asyncio
from apis import process_url
import json 


# ✅ CORRECT
async def main():
    test_url = "https://www.amazon.com/Stephen-Joseph-Pencil-Pouches-Shark/dp/B07G48YSVS/ref=hw_26_bac_dag_n_a99e?pf_rd_p=ebe34444-8d76-4f60-a7a6-8ce76221ad7e&pf_rd_r=55N4FDQB8DC3TPQGNCC8&sr=1-1-ee6b843e-c948-44ff-ba68-57de7583a49d&th=1"

    # Just use 'await' since we are already inside an async function
    result = await process_url(test_url) 
    
    print("\nFinal Output:")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    # This is the single, outer entry point that starts the event loop
    asyncio.run(main())