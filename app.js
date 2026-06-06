fetch("anime.json")
.then(r => r.json())
.then(data => {

    const wall = document.getElementById("wall");

    data.forEach(anime => {

        const a = document.createElement("a");

        a.href =
            `https://myanimelist.net/anime/${anime.id}`;

        a.target = "_blank";

        const img = document.createElement("img");

        img.src = anime.image;
        img.className = "poster";
        img.loading = "lazy";

        a.appendChild(img);

        wall.appendChild(a);
    });
});