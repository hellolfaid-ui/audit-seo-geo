#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vérification automatique des 3 signaux "invisibles" de l'audit SEO/GEO/SEA (36 domaines
concessions automobiles) : Schema.org/JSON-LD, LocalBusiness (pour GEO local), WebMCP
(pour Navigation agentique). 100% gratuit, open-source (requests + beautifulsoup4),
sans API ni compte à créer.

POURQUOI CE SCRIPT EXISTE : l'outil de fetch utilisé par Claude convertit le HTML en texte
lisible avant de le lire, ce qui supprime tout code invisible (balises <script>, y compris
le JSON-LD schema.org ET le code d'enregistrement WebMCP). Ce script lit le HTML brut tel
qu'il arrive du serveur — donc il voit tout, y compris ce qui est invisible pour un visiteur
humain. Il doit tourner sur une machine avec accès internet normal (la vôtre ou celle de
LFAID) : l'environnement sandbox de Claude bloque les connexions sortantes vers des domaines
externes, donc Claude ne peut pas l'exécuter lui-même.

USAGE :
    pip install requests beautifulsoup4
    python3 verif_schema_org.py

SORTIE :
    audit_signaux_invisibles.json  (détail complet par URL)
    audit_signaux_invisibles.csv   (résumé tableur, une ligne par URL)

Pour une exécution automatique chaque lundi sans intervention manuelle :
  - cron classique sur un serveur/PC toujours allumé
    (crontab -e -> "0 7 * * 1 /usr/bin/python3 /chemin/vers/verif_schema_org.py")
  - ou lancé une fois par semaine à la main (10 secondes pour 36 sites), fichier déposé
    dans un dossier Google Drive partagé que Claude lit chaque lundi.
"""

import json
import csv
import re
import time
from datetime import datetime, timezone

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit(
        "Dépendances manquantes. Installez-les avec :\n"
        "    pip install requests beautifulsoup4\n"
        "(gratuites, open-source, aucune clé API nécessaire)"
    )

# ---------------------------------------------------------------------------
# 1) Liste des URLs à vérifier — une ou plusieurs par domaine.
#    Ajoutez librement des pages concessions/stock pour affiner GEO local.
# ---------------------------------------------------------------------------
URLS = [
    "https://dewillermin.fr/",
    "https://mg-dewillermin.fr/",
    "https://mercedes-benz-dewillermin.fr/",
    "https://smart-dewillermin.fr/",
    "https://mercedes-huillier.fr/",
    "https://fordtrucks-huillier.fr/",
    "https://vehicules-speciaux-huillier.fr/",
    "https://hamecher.fr/",
    "https://mercedes-hamecher.fr/",
    "https://mg-hamecher.fr/",
    "https://mercedes-chevalley.ch/",
    "https://hyundai-chevalley.ch/",
    "https://volvo-chevalley.ch/",
    "https://nissan-chevalley.ch/",
    "https://smart-chevalley.ch/",
    "https://mazda-chevalley.ch/",
    "https://bentley-chevalley.ch/",
    "https://groupe-chevalley.ch/",
    "https://byd-kroely.fr/",
    "https://mercedes-kroely.fr/",
    "https://smart-kroely.fr/",
    "https://ok-occasionkroely.fr/",
    "https://bpmcars.fr/",
    "https://bpmcars.ch/",
    "https://bpmexclusive.com/",
    "https://bpmmotorbike.fr/",
    "https://bpmagri.fr/",
    "https://bpmpro.fr/",
    "https://astonmartinbordeaux.com/",
    "https://astonmartin-monaco.com/",
    "https://astonmartinparis.com/",
    "https://www.groupe-lempereur.com/",
    "https://mercedes-emb.fr/",
    "https://autoshop.fr/",
    "https://amplitude-auto.com/",
    "https://lfaid.fr/",
]

# @type schema.org pertinents pour la catégorie GEO local (LocalBusiness et ses sous-types)
LOCAL_BUSINESS_TYPES = {
    "LocalBusiness", "AutoDealer", "AutomotiveBusiness", "Organization",
    "AutoRepair", "AutoPartsStore", "MotorcycleDealer",
}
REVIEW_TYPES = {"Review", "AggregateRating"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AuditSEOGEO-Bot/1.0; +schema-org-check)"
}
TIMEOUT = 15


def extract_jsonld(html):
    """Retourne les blocs JSON-LD trouvés + les @type détectés (gère les blocs @graph)."""
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.find_all("script", type="application/ld+json")
    found_types = set()
    raw_blocks = []
    parse_errors = 0
    for b in blocks:
        raw = b.string or b.get_text() or ""
        raw_blocks.append(raw.strip()[:2000])
        try:
            data = json.loads(raw)
        except Exception:
            parse_errors += 1
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            candidates = graph if isinstance(graph, list) else [item]
            for c in candidates:
                if isinstance(c, dict) and "@type" in c:
                    t = c["@type"]
                    if isinstance(t, list):
                        found_types.update(t)
                    else:
                        found_types.add(t)
    return {
        "script_count": len(blocks),
        "types_found": sorted(found_types),
        "parse_errors": parse_errors,
        "raw_blocks": raw_blocks,
    }


def check_microdata(html):
    """Détection de microdata (itemscope/itemtype) en complément du JSON-LD."""
    return bool(re.search(r'itemscope|itemtype="https?://schema\.org/', html, re.I))


def check_webmcp(html):
    """Détection des indices WebMCP (Navigation agentique) :
    - attribut HTML data-webmcp
    - appel JS navigator.modelContext.registerTool
    Les deux sont invisibles pour un lecteur de contenu classique."""
    has_attr = bool(re.search(r'data-webmcp', html, re.I))
    has_js_call = bool(re.search(r'navigator\.modelContext\.registerTool', html, re.I))
    return {"data_webmcp_attribute": has_attr, "register_tool_call": has_js_call,
            "any_webmcp_signal": has_attr or has_js_call}


def main():
    results = []
    print(f"Vérification de {len(URLS)} URLs (Schema.org + LocalBusiness + WebMCP)...\n")
    for url in URLS:
        row = {"url": url, "checked_at": datetime.now(timezone.utc).isoformat()}
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            row["http_status"] = resp.status_code
            if resp.status_code >= 400:
                row["error"] = f"HTTP {resp.status_code}"
                print(f"  [ERREUR] {url} -> HTTP {resp.status_code}")
            else:
                html = resp.text
                jsonld = extract_jsonld(html)
                types_found = set(jsonld["types_found"])
                row["jsonld_script_count"] = jsonld["script_count"]
                row["schema_types_found"] = sorted(types_found)
                row["jsonld_parse_errors"] = jsonld["parse_errors"]
                row["has_microdata"] = check_microdata(html)
                row["has_any_schema"] = bool(types_found) or row["has_microdata"]

                # GEO local : LocalBusiness / AutoDealer et assimilés
                local_hit = types_found & LOCAL_BUSINESS_TYPES
                row["local_business_schema_found"] = sorted(local_hit)
                row["has_local_business_schema"] = bool(local_hit)

                # Avis clients : Review / AggregateRating
                review_hit = types_found & REVIEW_TYPES
                row["review_schema_found"] = sorted(review_hit)
                row["has_review_schema"] = bool(review_hit)

                # Navigation agentique : WebMCP
                webmcp = check_webmcp(html)
                row.update(webmcp)

                row["raw_jsonld_preview"] = jsonld["raw_blocks"]

                status = "OK" if row["has_any_schema"] else "AUCUN SCHEMA"
                print(f"  [{status}] {url}")
                print(f"      types: {sorted(types_found) or '(aucun)'}")
                print(f"      LocalBusiness: {'OUI' if local_hit else 'non'} | "
                      f"Review/Rating: {'OUI' if review_hit else 'non'} | "
                      f"WebMCP: {'OUI' if webmcp['any_webmcp_signal'] else 'non'}")
        except requests.exceptions.RequestException as e:
            row["error"] = str(e)
            print(f"  [ECHEC RESEAU] {url} -> {e}")
        results.append(row)
        time.sleep(1)  # poli avec les serveurs cibles

    with open("audit_signaux_invisibles.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    with open("audit_signaux_invisibles.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "URL", "HTTP Status", "Nb blocs JSON-LD", "Types schema.org détectés",
            "Microdata détectée", "A du schema (oui/non)",
            "LocalBusiness/AutoDealer détecté", "Review/AggregateRating détecté",
            "WebMCP détecté", "Erreur",
        ])
        for r in results:
            writer.writerow([
                r["url"],
                r.get("http_status", ""),
                r.get("jsonld_script_count", ""),
                "; ".join(r.get("schema_types_found", [])),
                "oui" if r.get("has_microdata") else "non",
                "OUI" if r.get("has_any_schema") else "NON",
                "OUI" if r.get("has_local_business_schema") else "NON",
                "OUI" if r.get("has_review_schema") else "NON",
                "OUI" if r.get("any_webmcp_signal") else "NON",
                r.get("error", ""),
            ])

    n_ok = sum(1 for r in results if r.get("has_any_schema"))
    n_local = sum(1 for r in results if r.get("has_local_business_schema"))
    n_review = sum(1 for r in results if r.get("has_review_schema"))
    n_webmcp = sum(1 for r in results if r.get("any_webmcp_signal"))
    print(f"\nTerminé sur {len(results)} URLs :")
    print(f"  Schema.org présent      : {n_ok}")
    print(f"  LocalBusiness/AutoDealer : {n_local}")
    print(f"  Review/AggregateRating  : {n_review}")
    print(f"  Signal WebMCP           : {n_webmcp}")
    print("Rapports écrits : audit_signaux_invisibles.json (détail) et .csv (résumé).")


if __name__ == "__main__":
    main()
