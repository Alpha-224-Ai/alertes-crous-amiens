import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup


# Recherche CROUS pour toute la zone d'Amiens
SEARCH_URL = (
    "https://trouverunlogement.lescrous.fr/tools/47/search"
    "?bounds=2.2235574_49.9505487_2.3457767_49.846837"
    "&locationName=Amiens+%2880000%29"
)

BASE_URL = "https://trouverunlogement.lescrous.fr"
STATE_FILE = "state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def get_total_pages(soup):
    title = soup.find("title")

    if title:
        match = re.search(
            r"page\s+\d+\s+sur\s+(\d+)",
            title.get_text(),
            re.IGNORECASE,
        )

        if match:
            return int(match.group(1))

    return 1


def parse_listings(soup):
    logements = []

    for card in soup.select("li.fr-col-lg-4"):
        link = card.select_one("h3.fr-card__title a")

        if not link:
            continue

        name = link.get_text(" ", strip=True)
        href = link.get("href", "")

        if not href:
            continue

        logement_id = href.rstrip("/").split("/")[-1]

        if href.startswith("http"):
            url = href
        else:
            url = BASE_URL + href

        address_tag = card.select_one("p.fr-card__desc")
        address = (
            address_tag.get_text(" ", strip=True)
            if address_tag
            else "Non précisée"
        )

        price_tag = card.select_one(".fr-badges-group .fr-badge")
        price = (
            price_tag.get_text(" ", strip=True)
            if price_tag
            else "Non précisé"
        )

        logements.append(
            {
                "id": logement_id,
                "name": name,
                "address": address,
                "price": price,
                "url": url,
            }
        )

    return logements


def get_details(session, logement):
    try:
        response = session.get(
            logement["url"],
            timeout=30,
        )
        response.raise_for_status()

        soup = BeautifulSoup(
            response.content,
            "html.parser",
            from_encoding="utf-8",
        )

        text = soup.get_text("\n", strip=True)

        surface_match = re.search(
            r"Superficie\s*:\s*([0-9.,]+\s*m²)",
            text,
            re.IGNORECASE,
        )

        if surface_match:
            logement["surface"] = surface_match.group(1)
        else:
            logement["surface"] = "Non précisée"

        h1 = soup.find("h1")

        if h1:
            titre = h1.get_text(" ", strip=True)

            if " - " in titre:
                logement["type"] = titre.split(" - ")[0]
            else:
                logement["type"] = titre
        else:
            logement["type"] = "Logement CROUS"

    except Exception as error:
        print(
            f"Impossible de récupérer les détails "
            f"de {logement['name']} : {error}"
        )

        logement["surface"] = "Non précisée"
        logement["type"] = "Logement CROUS"

    return logement


def fetch_logements():
    session = requests.Session()
    session.headers.update(HEADERS)

    response = session.get(
        SEARCH_URL,
        params={"page": 1},
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(
        response.content,
        "html.parser",
        from_encoding="utf-8",
    )

    total_pages = get_total_pages(soup)

    logements = parse_listings(soup)

    for page in range(2, total_pages + 1):
        time.sleep(1)

        response = session.get(
            SEARCH_URL,
            params={"page": page},
            timeout=30,
        )
        response.raise_for_status()

        soup = BeautifulSoup(
            response.content,
            "html.parser",
            from_encoding="utf-8",
        )

        logements.extend(parse_listings(soup))

    # Suppression des éventuels doublons
    uniques = {}

    for logement in logements:
        uniques[logement["id"]] = logement

    return session, list(uniques.values())


def load_previous_state():
    if not os.path.exists(STATE_FILE):
        return None

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        return set(data.get("current_ids", []))

    except Exception:
        return set()


def save_state(logements):
    current_ids = sorted(
        logement["id"]
        for logement in logements
    )

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            {"current_ids": current_ids},
            file,
            ensure_ascii=False,
            indent=2,
        )


def send_discord_alert(logement):
    webhook_url = os.environ.get(
        "DISCORD_WEBHOOK_URL"
    )

    if not webhook_url:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL n'est pas configuré."
        )

    message = {
        "content": (
            "🏠 **NOUVEAU LOGEMENT CROUS "
            "DISPONIBLE À AMIENS !**"
        ),
        "embeds": [
            {
                "title": logement["name"],
                "url": logement["url"],
                "description": (
                    "✅ **Disponible**\n\n"
                    f"🔗 **[VOIR / RÉSERVER "
                    f"CE LOGEMENT]({logement['url']})**"
                ),
                "fields": [
                    {
                        "name": "📍 Adresse",
                        "value": logement["address"],
                        "inline": False,
                    },
                    {
                        "name": "🏠 Type",
                        "value": logement["type"],
                        "inline": True,
                    },
                    {
                        "name": "📐 Surface",
                        "value": logement["surface"],
                        "inline": True,
                    },
                    {
                        "name": "💶 Loyer",
                        "value": logement["price"],
                        "inline": True,
                    },
                ],
                "footer": {
                    "text": (
                        "Source : "
                        "trouverunlogement.lescrous.fr"
                    )
                },
            }
        ],
    }

    response = requests.post(
        webhook_url,
        json=message,
        timeout=30,
    )

    response.raise_for_status()


def main():
    print("Recherche des logements CROUS à Amiens...")

    session, logements = fetch_logements()

    print(
        f"{len(logements)} logement(s) "
        "actuellement détecté(s)."
    )

    previous_ids = load_previous_state()
    if previous_ids is None:
        print(
            "Première exécution : création de la liste "
            "de référence. Aucune alerte envoyée."
        )
        save_state(logements)
        return

    nouveaux = [
        logement
        for logement in logements
        if logement["id"] not in previous_ids
    ]

    print(
        f"{len(nouveaux)} nouveau(x) logement(s)."
    )

    for logement in nouveaux:
        logement = get_details(
            session,
            logement,
        )

        print(
            "Nouvelle disponibilité : "
            f"{logement['name']}"
        )

        send_discord_alert(logement)

        # Petit délai pour éviter de solliciter Discord
        # trop rapidement s'il y a plusieurs logements.
        time.sleep(1)

    save_state(logements)

    print("Vérification terminée.")


if __name__ == "__main__":
    main()
