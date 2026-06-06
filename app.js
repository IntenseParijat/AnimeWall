fetch("anime.json")
.then(r => r.json())
.then(data => {

    const wall = document.getElementById("wall");

    data.forEach(anime => {

        const link = document.createElement("a");

        link.href = anime.url;
        link.target = "_blank";

        const img = document.createElement("img");

        img.src = anime.image;
        img.loading = "lazy";
        img.className = "poster";

        link.appendChild(img);

        wall.appendChild(link);

    });

});