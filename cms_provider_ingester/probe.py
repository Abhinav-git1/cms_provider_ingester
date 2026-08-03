import os, requests

DATASET_ID = os.getenv("CMS_DATASET_ID", "6fea9d79-0129-4e4c-b1b8-23cd86a4f435")
url = f"https://data.cms.gov/data-api/v1/dataset/{DATASET_ID}/data"

r = requests.get(url, params={"limit": 2, "offset": 0}, timeout=60)
r.raise_for_status()
rows = r.json()

print("rows:", len(rows))
if rows:
    print("sample keys:", list(rows[0].keys())[:15])

