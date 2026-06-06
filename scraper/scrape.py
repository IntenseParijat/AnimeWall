import json
import requests
from bs4 import BeautifulSoup

USERNAME = "ParijatSoftwares"

url = f"https://myanimelist.net/animelist/{USERNAME}"

html = requests.get(
    url,
    headers={
        "User-Agent":
        "Mozilla/5.0"
    }
).text

soup = BeautifulSoup(html, "html.parser")

anime = []

for link in soup.select("a.link.sort"):

    href = link.get("href","")

    if "/anime/" not in href:
        continue

    try:
        anime_id = href.split("/anime/")[1].split("/")[0]

        anime.append({
            "id": anime_id,
            "image":
            f"https://cdn.myanimelist.net/images/anime/{anime_id}.jpg"
        })

    except:
        pass

with open("anime.json","w") as f:
    json.dump(anime,f,indent=4)