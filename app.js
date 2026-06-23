const IMAGE_LOAD_TIMEOUT = 12000;
const LOADER_HOLD_DELAY = 400;

const loader = document.getElementById("loader");
const loaderPercent = document.getElementById("loader-percent");
const wall = document.getElementById("wall");

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
        setTimeout(finish, IMAGE_LOAD_TIMEOUT);
    });
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
        setLoaderProgress(35);

        let loadedImages = 0;
        const imagePromises = data.map(anime => {
            const { link, img } = createAnimeCard(anime);
            wall.appendChild(link);

            return waitForImage(img).then(() => {
                loadedImages += 1;
                const imageProgress = data.length ? (loadedImages / data.length) * 60 : 60;
                setLoaderProgress(35 + imageProgress);
            });
        });

        await Promise.all(imagePromises);
        await hideLoader();
    } catch (error) {
        console.error(error);
        wall.innerHTML = '<p class="error-message">Unable to load anime wall right now. Please try again later.</p>';
        await hideLoader();
    }
}

initAnimeWall();
