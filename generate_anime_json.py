import requests
import json
import time

USERNAME = "ParijatSoftwares"

statuses = [1, 2, 3, 4, 6]

all_anime = []
seen = set()

for status in statuses:

    print(f"Loading status {status}")

    url = (
        f"https://myanimelist.net/animelist/"
        f"{USERNAME}/load.json?status={status}"
    )

    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    anime_list = response.json()

    for anime in anime_list:

        mal_id = anime["anime_id"]

        if mal_id in seen:
            continue

        seen.add(mal_id)

        image = anime.get("anime_image_path", "")
        image = image.replace("/r/192x272", "")
        image = image.split("?")[0]
        base, ext = image.rsplit(".", 1)
        image = f"{base}l.{ext}"

        all_anime.append({
            "id": mal_id,
            "title": anime["anime_title"],
            "image": image,
            "url": f"https://myanimelist.net/anime/{mal_id}",
            "score": anime.get("score", 0),
            "status": anime.get("status", 0)
        })

    time.sleep(1)

with open(
    "anime.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        all_anime,
        f,
        ensure_ascii=False,
        indent=2
    )

print()
print(f"Saved {len(all_anime)} anime")
