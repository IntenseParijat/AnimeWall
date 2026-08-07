const IMAGE_LOAD_TIMEOUT = 12000;
const LOADER_HOLD_DELAY = 400;

const loader = document.getElementById("loader");
const loaderPercent = document.getElementById("loader-percent");
const wall = document.getElementById("wall");
const sortSelect = document.getElementById("sort");

let animeData = [];
let originalOrder = [];
let masonryGrid;
let displayedProgress = 0;
let targetProgress = 0;
let progressFrame;

const statusMap = {
    watching: {
        label: "Watching",
        className: "watching",
        sortOrder: 1
    },
    finished: {
        label: "Finished",
        className: "finished",
        sortOrder: 2
    },
    dropped: {
        label: "Dropped",
        className: "dropped",
        sortOrder: 4
    },
    on_hold: {
        label: "On Hold",
        className: "on-hold",
        sortOrder: 3
    },
    plan_to_watch: {
        label: "Plan to Watch",
        className: "plan-to-watch",
        sortOrder: 6
    }
};

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

    const normalizedKey = String(status).trim().toLowerCase().replace(/[\s-]+/g, "_");
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
    return normalizedStatus ? normalizedStatus.sortOrder : 0;
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

function waitForImage(img) {
    return new Promise(resolve => {
        let settled = false;
        const finish = () => {
            if (settled) {
                return;
            }

            settled = true;
            img.classList.add("loaded");
            resolve();
        };

        img.addEventListener("load", finish, { once: true });
        img.addEventListener("error", finish, { once: true });

        if (img.complete) {
            finish();
        }

        setTimeout(finish, IMAGE_LOAD_TIMEOUT);
    });
}

function createAnimeCard(anime, originalIndex) {
    const item = document.createElement("div");
    item.className = "masonry-item";
    item.dataset.originalIndex = String(originalIndex);
    item.dataset.score = String(getSortScore(anime.score));
    item.dataset.status = String(getStatusSortOrder(anime.status));
    item.dataset.title = (anime.title || "").trim();

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
    item.appendChild(link);

    return { item, img };
}

function getItemData(item) {
    const { dataset } = item.getElement();

    return {
        originalIndex: Number(dataset.originalIndex),
        score: Number(dataset.score),
        status: Number(dataset.status),
        title: dataset.title
    };
}

function compareMasonryItems(itemA, itemB, mode) {
    const a = getItemData(itemA);
    const b = getItemData(itemB);
    let comparison = 0;

    switch (mode) {
        case "rating-desc":
            comparison = b.score - a.score;
            break;
        case "rating-asc":
            comparison = a.score - b.score;
            break;
        case "status-desc":
            comparison = b.status - a.status;
            break;
        case "status-asc":
            comparison = a.status - b.status;
            break;
        case "title-desc":
            comparison = b.title.localeCompare(a.title, undefined, { sensitivity: "base" });
            break;
        case "title-asc":
            comparison = a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
            break;
        default:
            comparison = a.originalIndex - b.originalIndex;
    }

    return comparison || a.originalIndex - b.originalIndex;
}

function initializeMasonry() {
    if (typeof Muuri !== "function") {
        throw new Error("The masonry layout library could not be loaded.");
    }

    masonryGrid = new Muuri(wall, {
        items: ".masonry-item",
        layoutDuration: 450,
        layoutEasing: "ease",
        layoutOnResize: 120
    });

    masonryGrid.refreshItems().layout();
}

function sortAnime(mode) {
    if (!masonryGrid) {
        return;
    }

    masonryGrid.sort((itemA, itemB) => compareMasonryItems(itemA, itemB, mode));
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
        originalOrder = animeData.map((anime, originalIndex) => ({ anime, originalIndex }));
        setLoaderProgress(35);

        let loadedImages = 0;

        const imagePromises = originalOrder.map(({ anime, originalIndex }) => {
            const { item, img } = createAnimeCard(anime, originalIndex);
            wall.appendChild(item);

            return waitForImage(img).then(() => {
                loadedImages++;
                const imageProgress = data.length ? (loadedImages / data.length) * 60 : 60;
                setLoaderProgress(35 + imageProgress);
            });
        });

        await Promise.all(imagePromises);
        initializeMasonry();
        await hideLoader();
    } catch (error) {
        console.error(error);
        const message = document.createElement("p");
        message.className = "error-message";
        message.textContent = "Unable to load anime wall right now. Please try again later.";
        wall.replaceChildren(message);
        await hideLoader();
    }
}

initAnimeWall();

sortSelect.addEventListener("change", function () {
    sortAnime(this.value);
});
