#!/usr/bin/env python3
"""
Aniwatch.co.at Complete API Scraper - Updated
============================================
Refactored with generic scrape_page logic for all category endpoints.
"""

import re
import base64
import json
import requests
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
            
            html = resp.text
            data = {
                "success": True,
                "slug": slug,
                "title": "",
                "image": "",
                "description": "",
                "episode_nonce": None,
                "recent_episodes": []
            }
            
            title_match = re.search(r"<title>([^|]+)\s*\|\s*Aniwatch", html, re.I)
            if title_match:
                data["title"] = title_match.group(1).strip()
            
            img_match = re.search(r'og:image"[^>]+content="([^"]+)', html)
            if img_match:
                data["image"] = img_match.group(1)
            
            desc_match = re.search(r'og:description"[^>]+content="([^"]+)', html)
            if desc_match:
                data["description"] = desc_match.group(1)
            
            ep_nonce_match = re.search(r'hianime_ep_ajax\s*=\s*\{"ajax_url":"[^"]+","episode_nonce":"(\w+)"\}', html)
            if ep_nonce_match:
                data["episode_nonce"] = ep_nonce_match.group(1)
            
            ep_links = re.findall(
                r'href="(https?://aniwatch\.co\.at/[^"]+-episode-\d+-english-subbed/)"',
                html
            )
            
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
        """Get episode list for anime from REST API (max 100 recent episodes)"""
        try:
            resp = self.session.get(
                f"{REST_API}/posts",
                params={"search": f"{anime_title} episode", "per_page": 100},
                timeout=30
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}
            
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
                            episodes.append({
                                "number": ep_num,
                                "title": title,
                                "link": link
                            })
            
            episodes = sorted(episodes, key=lambda x: x["number"])
            return {"success": True, "episodes": episodes, "note": "Showing max 100 most recent episodes"}
            
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_episode_sources(self, episode_link: str) -> Dict[str, Any]:
        """Get video sources for an episode using WordPress REST API"""
        try:
            resp = self.session.get(episode_link, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}
            
            html = resp.text
            post_id_match = re.search(r'postid-(\d+)', html) or re.search(r'wp-json/wp/v2/posts/(\d+)', html)
            if not post_id_match:
                return {"success": False, "error": "Post ID not found"}
            
            post_id = post_id_match.group(1)
            api_resp = self.session.get(f"{BASE_URL}/wp-json/hianime/v1/episode/servers/{post_id}", timeout=30)
            
            if api_resp.status_code != 200:
                return {"success": False, "error": f"API Status {api_resp.status_code}"}
            
            response_data = api_resp.json()
            if not response_data.get("status"):
                return {"success": False, "error": "API returned error status"}
            
            html_content = response_data.get('html', '')
            servers = []
            for match in re.finditer(r'data-server-name="([^"]+)"[^>]+data-hash="([^"]+)"', html_content):
                name = match.group(1)
                h = match.group(2)
                try:
                    decoded = base64.b64decode(h).decode('utf-8')
                    servers.append({
                        "name": name,
                        "hash": h,
                        "url": decoded,
                        "type": "sub" if "/sub" in decoded else "dub"
                    })
                except: continue
            
            return {"success": True, "post_id": post_id, "servers": servers}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_episode_sources_by_id(self, post_id: str, nonce: str = None) -> Dict[str, Any]:
        """Get servers by post_id directly via REST API"""
        try:
            api_resp = self.session.get(f"{BASE_URL}/wp-json/hianime/v1/episode/servers/{post_id}", timeout=30)
            response_data = api_resp.json()
            html_content = response_data.get("html", "")
            servers = []
            
            for match in re.finditer(r'data-server-name="([^"]+)"[^>]+data-hash="([^"]+)"', html_content):
                name, h = match.group(1), match.group(2)
                try:
                    stream_url = base64.b64decode(h).decode("utf-8")
                    servers.append({"name": name, "url": stream_url, "type": "sub" if "/sub" in stream_url else "dub"})
                except: continue
            
            return {"success": True, "post_id": post_id, "servers": servers}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_stream_url(self, stream_url: str) -> Dict[str, Any]:
        """Get m3u8 URL from stream page using megaplay.buzz getSources API"""
        try:
            resp = self.session.get(stream_url, headers={"Referer": BASE_URL}, timeout=30, allow_redirects=True)
            html = resp.text
            
            # Direct check
            m3u8_match = re.search(r'(https?://[^\s"<>]+\.m3u8[^\s"<>]*)', html)
            if m3u8_match:
                return {"success": True, "m3u8_url": m3u8_match.group(1)}
            
            # megaplay.buzz logic
            iframe_match = re.search(r'<iframe[^>]+src="([^"]+)"', html)
            if iframe_match:
                iframe_url = iframe_match.group(1)
                mega_resp = self.session.get(iframe_url, headers={"Referer": "https://1anime.site/"}, timeout=30)
                cid_match = re.search(r'cid\s*:\s*["\x27]([^"\x27]+)["\x27]', mega_resp.text)
                
                if cid_match:
                    cid = cid_match.group(1)
                    sources_resp = self.session.get(f"https://megaplay.buzz/stream/getSources?id={cid}", headers={"Referer": iframe_url, "X-Requested-With": "XMLHttpRequest"}, timeout=30)
                    sources_data = sources_resp.json()
                    m3u8 = sources_data.get("sources", {}).get("file", "")
                    
                    tracks = []
                    for t in sources_data.get("tracks", []):
                        if t.get("kind") in ["captions", "subtitles"]:
                            tracks.append({"url": t.get("file", ""), "lang": t.get("label", "en").lower(), "label": t.get("label", "English")})
                    
                    return {"success": True, "m3u8_url": m3u8, "tracks": tracks, "type": "hls"}

            return {"success": False, "error": "No stream found"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ========== NEW SCRAPER LOGIC ==========

    def scrape_page(self, url: str) -> Dict[str, Any]:
        """Generic Scraper for Anime Cards"""
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            html = resp.text
            results = []
            pattern = re.findall(
                r'<a href="([^"]+)" class="film-poster">.*?<img[^>]+data-src="([^"]+)"[^>]+alt="([^"]+)"',
                html, re.S | re.I
            )

            seen = set()
            for item in pattern:
                link, poster, title = item[0], item[1], item[2]
                anime_name = re.sub(r"\s+English\s+(Sub|Dub).*$", "", title).strip()

                if anime_name in seen: continue
                seen.add(anime_name)

                results.append({
                    "title": anime_name,
                    "link": link,
                    "slug": self._title_to_slug(anime_name),
                    "poster": poster
                })

            return {"success": True, "anime": results, "page": 1}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_home(self) -> Dict[str, Any]:
        return self.scrape_page(BASE_URL)

    def get_most_popular(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/most-popular/")

    def get_top_airing(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/top-airing/")

    def get_recently_updated(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/recently-updated/")

    def get_recently_added(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/recently-added/")

    def get_completed(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/completed/")

    def get_subbed_anime(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/subbed/")

    def get_dubbed_anime(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/dubbed/")

    def get_movies(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/movie/")

    def get_ova(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/ova/")

    def get_ona(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/ona/")

    def get_specials(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/special/")

    def get_tv_series(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/tv-series/")

    # --- Extra List Helpers ---

    def get_az_list(self, letter: str = "all", page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/az-list/{letter}" if letter != "all" else f"{BASE_URL}/az-list/")

    def get_genres(self) -> Dict[str, Any]:
        return {"success": True, "genres": ["action", "comedy", "drama", "fantasy", "romance", "sci-fi"]}

    def get_by_genre(self, genre: str, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/genre/{genre}/")

    def get_random_anime(self) -> Dict[str, Any]:
        return self.get_home() # Placeholder

    def get_schedules(self) -> Dict[str, Any]:
        return self.get_home() # Placeholder

    def get_filter_options(self) -> Dict[str, Any]:
        return {"success": True, "filters": {"types": ["sub", "dub"], "status": ["completed", "airing"]}}

    def get_most_favorite(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_top_upcoming(self, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/top-upcoming/")

    def get_by_producer(self, producer: str, page: int = 1) -> Dict[str, Any]:
        return self.scrape_page(f"{BASE_URL}/producer/{producer}/")

    def get_suggestions(self, keyword: str) -> Dict[str, Any]:
        return self.search(keyword, 5)


def create_app():
    """Create Flask API app"""
    from flask import Flask, request, jsonify
    app = Flask(__name__)
    api = AniwatchAPI()

    @app.route('/')
    def index():
        return jsonify({"name": "Aniwatch.co.at API", "version": "2.1"})

    @app.route('/search')
    def search():
        return jsonify(api.search(request.args.get('keyword', ''), int(request.args.get('limit', 10))))

    @app.route('/info/<slug>')
    def info(slug):
        return jsonify(api.get_anime_info(slug))

    @app.route('/episodes/<slug>')
    def episodes(slug):
        info = api.get_anime_info(slug)
        title = info.get("title", "").split(" - ")[0]
        return jsonify(api.get_episodes(title))

    @app.route('/sources')
    def sources():
        return jsonify(api.get_episode_sources(request.args.get('episode_link', '')))

    @app.route('/stream')
    def stream():
        return jsonify(api.get_stream_url(request.args.get('url', '')))

    # List Routes
    @app.route('/home')
    def home(): return jsonify(api.get_home())
    @app.route('/movies')
    def movies(): return jsonify(api.get_movies())
    @app.route('/most-popular')
    def most_popular(): return jsonify(api.get_most_popular())
    @app.route('/top-airing')
    def top_airing(): return jsonify(api.get_top_airing())
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

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)

app = create_app() # For Vercel
