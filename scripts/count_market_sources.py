def count_sources(row):
    rows = row.get('raw_bucket_offers') or []
    if not isinstance(rows, list):
        rows = []
    names = set()
    for item in rows:
        if isinstance(item, dict) and item.get('book'):
            names.add(str(item.get('book')).strip().lower())
    return len(names)
