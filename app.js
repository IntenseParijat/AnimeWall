const LOADER_HOLD_DELAY = 400;

const loader = document.getElementById("loader");
const loaderPercent = document.getElementById("loader-percent");
const wall = document.getElementById("wall");
const sortSelect = document.getElementById("sort");

let animeData = [];
let originalOrder = [];
const cardByAnime = new Map();
const originalIndexByAnime = new Map();
let masonry;
let displayedProgress = 0;
let targetProgress = 0;
let progressFrame;

const statusMap = {
    watching: {
        label: "Watching",
        className: "watching"
    },
    finished: {
        label: "Finished",
        className: "finished"
    },
    dropped: {
        label: "Dropped",
        className: "dropped"
    },
    on_hold: {
        label: "On Hold",
        className: "on-hold"
    },
    plan_to_watch: {
        label: "Plan to Watch",
        className: "plan-to-watch"
    }
};

const statusSortOrder = new Map([
    [statusMap.watching, 1],
    [statusMap.finished, 2],
    [statusMap.on_hold, 3],
    [statusMap.dropped, 4],
    [statusMap.plan_to_watch, 6]
]);

const statusAliases = new Map([
    ["1", "watching"],
    ["watching", "watching"],
    ["currently_watching", "watching"],
    ["currently watching", "watching"],
    ["in_progress", "watching"],
    ["in progress", "watching"],
    ["2", "finished"],
    ["finished", "finished"],
    ["completed", "finished"],
    ["complete", "finished"],
    ["4", "dropped"],
    ["dropped", "dropped"],
    ["3", "on_hold"],
    ["on_hold", "on_hold"],
    ["on hold", "on_hold"],
    ["paused", "on_hold"],
    ["6", "plan_to_watch"],
    ["plan_to_watch", "plan_to_watch"],
    ["plan to watch", "plan_to_watch"],
    ["planned", "plan_to_watch"],
    ["ptw", "plan_to_watch"]
]);

function normalizeStatus(status) {
    if (status === null || status === undefined) {
        return null;
    }

    const normalizedKey = String(status).trim().toLowerCase().replace(/-/g, "_");
    const canonicalStatus = statusAliases.get(normalizedKey);

    return canonicalStatus ? statusMap[canonicalStatus] : null;
}

function getDisplayScore(score) {
    const numericScore = Number(score);

    if (!Number.isFinite(numericScore) || numericScore <= 0) {
        return null;
    }

    return Number.isInteger(numericScore) ? String(numericScore) : numericScore.toFixed(1);
}

function getSortScore(score) {
    const numericScore = Number(score);
    return Number.isFinite(numericScore) ? numericScore : 0;
}

function getStatusSortOrder(status) {
    const normalizedStatus = normalizeStatus(status);
    return statusSortOrder.get(normalizedStatus) || 0;
}

function setLoaderProgress(progress) {
    targetProgress = Math.max(targetProgress, Math.min(progress, 100));
}

function animateLoaderProgress() {
    const delta = targetProgress - displayedProgress;

    if (Math.abs(delta) > 0.1) {
        displayedProgress += delta * 0.12;
    } else {
        displayedProgress = targetProgress;
    }

    const roundedProgress = Math.round(displayedProgress);
    loaderPercent.textContent = `${roundedProgress}%`;
    loader.style.setProperty("--loader-progress", `${roundedProgress}%`);
    progressFrame = requestAnimationFrame(animateLoaderProgress);
}

function createAnimeCard(anime) {
    const link = document.createElement("a");
    link.href = anime.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.className = "anime-card";
    link.setAttribute("aria-label", anime.title ? `Open ${anime.title} on MyAnimeList` : "Open anime details");

    const img = document.createElement("img");
    img.src = anime.image;
    img.alt = anime.title || "Anime poster";
    img.loading = "eager";
    img.className = "poster";

    const status = normalizeStatus(anime.status);
    if (status) {
        const badge = document.createElement("span");
        badge.className = `status-badge status-${status.className}`;
        badge.textContent = status.label;
        link.appendChild(badge);
    }

    const score = getDisplayScore(anime.score);
    if (score) {
        const rating = document.createElement("span");
        rating.className = "rating-badge";
        rating.setAttribute("aria-label", `Score ${score}`);
        rating.textContent = `⭐ ${score}`;
        link.appendChild(rating);
    }

    link.appendChild(img);

    return { link, img };
}

function compareAnime(a, b, mode) {
    let comparison = 0;

    switch (mode) {
        case "rating-desc":
            comparison = getSortScore(b.score) - getSortScore(a.score);
            break;
        case "rating-asc":
            comparison = getSortScore(a.score) - getSortScore(b.score);
            break;
        case "status-desc":
            comparison = getStatusSortOrder(b.status) - getStatusSortOrder(a.status);
            break;
        case "status-asc":
            comparison = getStatusSortOrder(a.status) - getStatusSortOrder(b.status);
            break;
        case "title-desc":
            comparison = String(b.title || "").localeCompare(String(a.title || ""), undefined, { sensitivity: "base" });
            break;
        case "title-asc":
            comparison = String(a.title || "").localeCompare(String(b.title || ""), undefined, { sensitivity: "base" });
            break;
        default:
            comparison = originalIndexByAnime.get(a) - originalIndexByAnime.get(b);
    }

    return comparison || originalIndexByAnime.get(a) - originalIndexByAnime.get(b);
}

function initializeMasonry() {
    if (typeof Masonry !== "function") {
        throw new Error("The masonry layout library could not be loaded.");
    }

    masonry = new Masonry(wall, {
        itemSelector: ".anime-card",
        columnWidth: ".anime-card",
        gutter: 12,
        percentPosition: true,
        transitionDuration: "0.45s"
    });
}

function sortAnime(mode) {
    if (!masonry) {
        return;
    }

    animeData = mode === "default" ? originalOrder.slice() : animeData.sort((a, b) => compareAnime(a, b, mode));

    animeData.forEach(anime => {
        wall.appendChild(cardByAnime.get(anime));
    });

    masonry.reloadItems();
    masonry.layout();
}

async function hideLoader() {
    setLoaderProgress(100);

    await new Promise(resolve => {
        const checkComplete = () => {
            if (displayedProgress >= 99.5) {
                loaderPercent.textContent = "100%";
                resolve();
            } else {
                requestAnimationFrame(checkComplete);
            }
        };
        checkComplete();
    });

    await new Promise(resolve => setTimeout(resolve, LOADER_HOLD_DELAY));
    loader.classList.add("loader-hidden");
    document.body.classList.remove("is-loading");
    loader.addEventListener("transitionend", () => {
        loader.setAttribute("aria-hidden", "true");
        loader.remove();
        cancelAnimationFrame(progressFrame);
    }, { once: true });
}

async function initAnimeWall() {
    document.body.classList.add("is-loading");
    animateLoaderProgress();
    setLoaderProgress(8);

    try {
        const response = await fetch("anime.json");
        setLoaderProgress(20);

        if (!response.ok) {
            throw new Error(`Unable to load anime.json: ${response.status}`);
        }

        const data = await response.json();

        if (!Array.isArray(data)) {
            throw new Error("anime.json must contain an array of anime entries.");
        }

        animeData = data;
        originalOrder = animeData.slice();
        originalOrder.forEach((anime, originalIndex) => {
            originalIndexByAnime.set(anime, originalIndex);
        });
        setLoaderProgress(35);

        let loadedImages = 0;

        originalOrder.forEach(anime => {
            const { link } = createAnimeCard(anime);
            cardByAnime.set(anime, link);
            wall.appendChild(link);
        });

        if (typeof imagesLoaded !== "function") {
            throw new Error("The image loading library could not be loaded.");
        }

        const imageLoader = imagesLoaded(wall);
        imageLoader.on("progress", (instance, image) => {
            image.img.classList.add("loaded");
            loadedImages++;
            const imageProgress = data.length ? (loadedImages / data.length) * 60 : 60;
            setLoaderProgress(35 + imageProgress);
        });

        await new Promise(resolve => imageLoader.on("always", resolve));
        initializeMasonry();
        await hideLoader();
    } catch (error) {
        console.error(error);
        const message = document.createElement("p");
        message.className = "error-message";
        message.textContent = "Unable to load anime wall right now. Please try again later.";
        if (!wall.children.length) {
            wall.appendChild(message);
        }
        await hideLoader();
    }
}

initAnimeWall();

sortSelect.addEventListener("change", function () {
    sortAnime(this.value);
});
