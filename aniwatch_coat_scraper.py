#!/usr/bin/env python3
"""
Aniwatch.co.at Complete API Scraper
==================================
Full working implementation based on code analysis.
UPDATED: Restored all missing routes and updated API index.
"""

import re
import base64
import json
import requests
import html
from urllib.parse import urljoin
from typing import Dict, List, Optional, Any

BASE_URL = "https://aniwatch.co.at"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
REST_API = f"{BASE_URL}/wp-json/wp/v2"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class AniwatchAPI:
    """Aniwatch.co.at API client"""
    
    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": HEADERS["User-Agent"],
            "Accept": HEADERS["Accept"],
            "Accept-Language": HEADERS["Accept-Language"],
        })
    
    def search(self, keyword: str, limit: int = 10) -> Dict[str, Any]:
        """Search anime by keyword"""
        try:
            resp = self.session.get(
                f"{REST_API}/posts",
                params={"search": keyword, "per_page": limit * 2},
                timeout=30
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}
            
            posts = resp.json()
            seen = set()
            results = []
            
            for post in posts:
                link = post.get("link", "")
                title = post.get("title", {}).get("rendered", "")
                
                if " Episode " in title:
                    anime_name = title.split(" Episode ")[0]
                else:
                    anime_name = title
                
                anime_name = re.sub(r"\s+English\s+(Sub|Dub).*$", "", anime_name).strip()
                
                if anime_name and anime_name not in seen:
                    seen.add(anime_name)
                    slug = self._title_to_slug(anime_name)
                    results.append({
                        "title": anime_name,
                        "slug": slug,
                        "link": link
                    })
            
            return {"success": True, "results": results[:limit]}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _title_to_slug(self, title: str) -> str:
        """Convert title to URL slug"""
        slug = title.lower()
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug.strip("-")
    
    def get_anime_info(self, slug: str) -> Dict[str, Any]:
        """Get anime details from anime page"""
        try:
            url = f"{BASE_URL}/anime/{slug}/"
            resp = self.session.get(url, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}
            
            html_text = resp.text
            data = {
                "success": True,
                "slug": slug,
                "title": "",
                "image": "",
                "description": "",
                "episode_nonce": None,
                "recent_episodes": []
            }
            
            title_match = re.search(r"<title>([^|]+)\s*\|\s*Aniwatch", html_text, re.I)
            if title_match: data["title"] = title_match.group(1).strip()
            
            img_match = re.search(r'og:image"[^>]+content="([^"]+)', html_text)
            if img_match: data["image"] = img_match.group(1)
            
            desc_match = re.search(r'og:description"[^>]+content="([^"]+)', html_text)
            if desc_match: data["description"] = desc_match.group(1)
            
            ep_nonce_match = re.search(r'hianime_ep_ajax\s*=\s*\{"ajax_url":"[^"]+","episode_nonce":"(\w+)"\}', html_text)
            if ep_nonce_match: data["episode_nonce"] = ep_nonce_match.group(1)
            
            ep_links = re.findall(r'href="(https?://aniwatch\.co\.at/[^"]+-episode-\d+-english-subbed/)"', html_text)
            episodes = []
            for link in list(set(ep_links[:100])):
                ep_match = re.search(r"-episode-(\d+)-", link)
                if ep_match:
                    ep_num = int(ep_match.group(1))
                    episodes.append({"number": ep_num, "link": link})
            
            data["recent_episodes"] = sorted(episodes, key=lambda x: x["number"])
            return data
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_episodes(self, anime_title: str) -> Dict[str, Any]:
        """Get episode list for anime from REST API"""
        try:
            resp = self.session.get(f"{REST_API}/posts", params={"search": f"{anime_title} episode", "per_page": 100}, timeout=30)
            if resp.status_code != 200: return {"success": False, "error": f"Status {resp.status_code}"}
            posts = resp.json()
            episodes = []
            for post in posts:
                title = post.get("title", {}).get("rendered", "")
                link = post.get("link", "")
                if f"{anime_title}" in title and "Episode" in title:
                    match = re.search(r"Episode\s+(\d+)", title)
                    if match:
                        ep_num = int(match.group(1))
                        if ep_num not in [e["number"] for e in episodes]:
                            episodes.append({"number": ep_num, "title": title, "link": link})
            return {"success": True, "episodes": sorted(episodes, key=lambda x: x["number"])}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_episode_sources(self, episode_link: str) -> Dict[str, Any]:
        """Get video sources for an episode"""
        try:
            resp = self.session.get(episode_link, timeout=30)
            if resp.status_code != 200: return {"success": False, "error": f"Status {resp.status_code}"}
            html_text = resp.text
            post_id_match = re.search(r'postid-(\d+)', html_text) or re.search(r'wp-json/wp/v2/posts/(\d+)', html_text)
            if not post_id_match: return {"success": False, "error": "Post ID not found"}
            post_id = post_id_match.group(1)
            api_resp = self.session.get(f"{BASE_URL}/wp-json/hianime/v1/episode/servers/{post_id}", timeout=30)
            response_data = api_resp.json()
            html_content = response_data.get('html', '')
            servers = []
            for match in re.finditer(r'data-server-name="([^"]+)"[^>]+data-hash="([^"]+)"', html_content):
                try:
                    decoded = base64.b64decode(match.group(2)).decode('utf-8')
                    servers.append({"name": match.group(1), "hash": match.group(2), "url": decoded, "type": "sub" if "/sub" in decoded else "dub"})
                except: continue
            return {"success": True, "post_id": post_id, "servers": servers}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_episode_sources_by_id(self, post_id: str, nonce: str = None) -> Dict[str, Any]:
        try:
            api_resp = self.session.get(f"{BASE_URL}/wp-json/hianime/v1/episode/servers/{post_id}", timeout=30)
            response_data = api_resp.json()
            html_content = response_data.get("html", "")
            servers = []
            for match in re.finditer(r'data-server-name="([^"]+)"[^>]+data-hash="([^"]+)"', html_content):
                try:
                    stream_url = base64.b64decode(match.group(2)).decode("utf-8")
                    servers.append({"name": match.group(1), "url": stream_url, "type": "sub" if "/sub" in stream_url else "dub"})
                except: continue
            return {"success": True, "post_id": post_id, "servers": servers}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_stream_url(self, stream_url: str) -> Dict[str, Any]:
        """Get m3u8 URL from stream page using megaplay.buzz getSources API"""
        try:
            resp = self.session.get(stream_url, headers={"Referer": BASE_URL}, timeout=30, allow_redirects=True)
            html_text = resp.text
            m3u8_match = re.search(r'(https?://[^\s"<>]+\.m3u8[^\s"<>]*)', html_text)
            if m3u8_match: return {"success": True, "m3u8_url": m3u8_match.group(1)}
            
            iframe_match = re.search(r'<iframe[^>]+src="([^"]+)"', html_text)
            if iframe_match:
                iframe_url = iframe_match.group(1)
                mega_resp = self.session.get(iframe_url, headers={"Referer": "https://1anime.site/"}, timeout=30)
                cid_match = re.search(r'cid\s*:\s*["\x27]([^"\x27]+)["\x27]', mega_resp.text)
                if cid_match:
                    cid = cid_match.group(1)
                    sources_resp = self.session.get(f"https://megaplay.buzz/stream/getSources?id={cid}", headers={"Referer": iframe_url, "X-Requested-With": "XMLHttpRequest"}, timeout=30)
                    sources_data = sources_resp.json()
                    m3u8 = sources_data.get("sources", {}).get("file", "")
                    tracks = [{"url": t.get("file", ""), "lang": t.get("label", "en").lower(), "label": t.get("label", "English")} 
                              for t in sources_data.get("tracks", []) if t.get("kind") in ["captions", "subtitles"]]
                    return {"success": True, "m3u8_url": m3u8, "tracks": tracks, "type": "hls"}
            return {"success": False, "error": "No stream found"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ========== GENERIC SCRAPER METHOD ==========

    def scrape_page(self, url: str) -> Dict[str, Any]:
        """Generic scraper for anime card pages"""
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code != 200: return {"success": False, "error": f"Status {resp.status_code}"}
            html_content = resp.text
            results = []
            pattern = re.findall(r'<a href="([^"]+)" class="film-poster">.*?<img[^>]+(?:data-src|src)="([^"]+)"[^>]+alt="([^"]+)"', html_content, re.S | re.I)
            seen = set()
            for item in pattern:
                link, poster, raw_title = item[0], html.unescape(item[1]), html.unescape(item[2])
                anime_name = re.sub(r"\s+English\s+(Sub|Dub).*$", "", raw_title).strip()
                if anime_name in seen: continue
                seen.add(anime_name)
                results.append({"title": anime_name, "link": link, "slug": self._title_to_slug(anime_name), "poster": poster})
            return {"success": True, "anime": results, "page": 1}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ========== SCRAPER ENDPOINTS ==========

    def get_home(self) -> Dict[str, Any]: return self.scrape_page(BASE_URL)
    def get_most_popular(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/most-popular/")
    def get_top_airing(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/top-airing/")
    def get_recently_updated(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/recently-updated/")
    def get_recently_added(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/recently-added/")
    def get_completed(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/completed/")
    def get_subbed_anime(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/subbed/")
    def get_dubbed_anime(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/dubbed/")
    def get_movies(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/movie/")
    def get_ova(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/ova/")
    def get_tv_series(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/tv-series/")
    def get_ona(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/ona/")
    def get_specials(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/special/")
    def get_by_genre(self, genre: str, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/genre/{genre}/")
    def get_top_upcoming(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/top-upcoming/")
    def get_by_producer(self, producer: str, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/producer/{producer}/")
    
    # Existing Helper Wrappers
    def get_az_list(self, letter: str = "all", page: int = 1) -> Dict[str, Any]:
        if letter == "all": return self.get_home()
        return self.scrape_page(f"{BASE_URL}/az-list/{letter}")
    def get_filter_options(self) -> Dict[str, Any]: return {"success": True, "filters": {"types": ["sub", "dub"], "status": ["completed", "airing"]}}
    def get_most_favorite(self, page: int = 1) -> Dict[str, Any]: return self.scrape_page(f"{BASE_URL}/most-favorite/")
    def get_suggestions(self, keyword: str) -> Dict[str, Any]: return self.search(keyword, 5)
    def get_random_anime(self) -> Dict[str, Any]:
        resp = self.session.get(f"{REST_API}/posts", params={"per_page": 50}, timeout=30)
        import random
        posts = resp.json()
        if posts:
            p = random.choice(posts)
            return {"success": True, "anime": {"title": p.get("title", {}).get("rendered", ""), "link": p.get("link", "")}}
        return {"success": False, "error": "No anime found"}


def create_app():
    from flask import Flask, request, jsonify
    app = Flask(__name__)
    api = AniwatchAPI()

    @app.route('/')
    def index():
        return jsonify({
            "name": "Aniwatch.co.at API",
            "version": "2.0",
            "source": "aniwatch.co.at",
            "endpoints": {
                "/": "API Index",
                "/home": "Home page",
                "/search?keyword=...": "Search anime",
                "/info/<slug>": "Get anime info",
                "/episodes/<slug>": "Get episode list",
                "/sources?episode_link=...": "Get video sources",
                "/stream?url=...": "Get m3u8 stream",
                "/movies": "Anime movies",
                "/tv-series": "TV series",
                "/ova": "OVA series",
                "/ona": "ONA series",
                "/special": "Specials",
                "/most-popular": "Most popular",
                "/top-airing": "Top airing",
                "/top-upcoming": "Top upcoming",
                "/recently-updated": "Recently updated",
                "/recently-added": "Recently added",
                "/completed": "Completed anime",
                "/subbed": "Subbed anime",
                "/dubbed": "Dubbed anime",
                "/genre/<genre>": "Anime by genre",
                "/producer/<producer>": "Anime by producer",
                "/az-list": "A-Z list",
                "/az/<letter>": "A-Z by letter",
                "/random": "Random anime",
                "/schedules": "Schedules",
                "/filter-options": "Filter options",
                "/most-favorite": "Most favorite",
                "/suggestions": "Search suggestions"
            }
        })

    # RESTORED AND PATCHED ROUTES
    @app.route('/home')
    def home(): return jsonify(api.get_home())

    @app.route('/movies')
    def movies(): return jsonify(api.get_movies())

    @app.route('/tv-series')
    def tv_series(): return jsonify(api.get_tv_series())

    @app.route('/ova')
    def ova(): return jsonify(api.get_ova())

    @app.route('/ona')
    def ona(): return jsonify(api.get_ona())

    @app.route('/special')
    def special(): return jsonify(api.get_specials())

    @app.route('/most-popular')
    def most_popular(): return jsonify(api.get_most_popular())

    @app.route('/top-airing')
    def top_airing(): return jsonify(api.get_top_airing())

    @app.route('/top-upcoming')
    def top_upcoming(): return jsonify(api.get_top_upcoming())

    @app.route('/recently-updated')
    def recently_updated(): return jsonify(api.get_recently_updated())

    @app.route('/recently-added')
    def recently_added(): return jsonify(api.get_recently_added())

    @app.route('/completed')
    def completed(): return jsonify(api.get_completed())

    @app.route('/subbed')
    def subbed(): return jsonify(api.get_subbed_anime())

    @app.route('/dubbed')
    def dubbed(): return jsonify(api.get_dubbed_anime())

    @app.route('/genre/<genre>')
    def genre(genre): return jsonify(api.get_by_genre(genre))

    @app.route('/producer/<producer>')
    def producer(producer): return jsonify(api.get_by_producer(producer))

    @app.route('/az-list')
    @app.route('/az/<letter>')
    def az_list(letter="all"): return jsonify(api.get_az_list(letter))

    @app.route('/random')
    def random_anime(): return jsonify(api.get_random_anime())

    @app.route('/schedules')
    def schedules(): return jsonify(api.get_home()) # Placeholder

    @app.route('/filter-options')
    def filter_options(): return jsonify(api.get_filter_options())

    @app.route('/most-favorite')
    def most_favorite(): return jsonify(api.get_most_favorite())

    @app.route('/suggestions')
    def suggestions(): return jsonify(api.get_suggestions(request.args.get('keyword', '')))

    # CORE ROUTES
    @app.route('/search')
    def search(): return jsonify(api.search(request.args.get('keyword', ''), int(request.args.get('limit', 10))))

    @app.route('/info/<slug>')
    def info(slug): return jsonify(api.get_anime_info(slug))

    @app.route('/episodes/<slug>')
    def episodes(slug):
        info_data = api.get_anime_info(slug)
        if not info_data.get("success"): return jsonify(info_data), 400
        title = info_data.get("title", "").split(" - ")[0].strip()
        return jsonify(api.get_episodes(title))

    @app.route('/sources')
    def sources():
        link = request.args.get('episode_link', '')
        if link: return jsonify(api.get_episode_sources(link))
        return jsonify(api.get_episode_sources_by_id(request.args.get('episode_id', '')))

    @app.route('/stream')
    def stream(): return jsonify(api.get_stream_url(request.args.get('url', '')))

    return app

# Standalone function wrappers
def search_anime(k, l=10): return AniwatchAPI().search(k, l)
def get_anime_info(s): return AniwatchAPI().get_anime_info(s)
def get_episodes(t): return AniwatchAPI().get_episodes(t)
def get_episode_sources(l): return AniwatchAPI().get_episode_sources(l)
def get_stream_url(u): return AniwatchAPI().get_stream_url(u)

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)

app = create_app()
