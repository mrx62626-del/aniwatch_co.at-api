#!/usr/bin/env python3

import re
import html
import base64
import json
import requests
from urllib.parse import urljoin
from typing import Dict, List, Optional, Any
from flask import Flask, jsonify, request

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

    def get_poster_from_slug(self, slug: str):
        try:
            url = f"{BASE_URL}/anime/{slug}/"
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                return ""
            html_content = resp.text
            poster_match = re.search(r'property="og:image" content="([^"]+)"', html_content)
            if poster_match:
                return html.unescape(poster_match.group(1))
            return ""
        except:
            return ""

    def search(self, keyword: str, limit: int = 10) -> Dict[str, Any]:
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
                        "link": link,
                        "poster": self.get_poster_from_slug(slug)
                    })

            return {"success": True, "results": results[:limit]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _title_to_slug(self, title: str) -> str:
        slug = title.lower()
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug.strip("-")

    def get_anime_info(self, slug: str) -> Dict[str, Any]:
        try:
            url = f"{BASE_URL}/anime/{slug}/"
            resp = self.session.get(url, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            html_content = resp.text
            data = {
                "success": True,
                "slug": slug,
                "title": "",
                "image": "",
                "description": "",
                "episode_nonce": None,
                "recent_episodes": []
            }

            title_match = re.search(r"<title>([^|]+)\s*\|\s*Aniwatch", html_content, re.I)
            if title_match:
                data["title"] = title_match.group(1).strip()

            img_match = re.search(r'og:image"[^>]+content="([^"]+)', html_content)
            if img_match:
                data["image"] = html.unescape(img_match.group(1))

            desc_match = re.search(r'og:description"[^>]+content="([^"]+)', html_content)
            if desc_match:
                data["description"] = html.unescape(desc_match.group(1))

            ep_links = re.findall(r'href="(https?://aniwatch\.co\.at/[^"]+-episode-\d+-english-sub/)"', html_content)
            episodes = []
            ep_num_list = []

            for link in list(set(ep_links[:100])):
                ep_match = re.search(r"-episode-(\d+)-", link)
                if ep_match:
                    ep_num = int(ep_match.group(1))
                    if ep_num not in ep_num_list:
                        ep_num_list.append(ep_num)
                        episodes.append({"number": ep_num, "link": link})

            data["recent_episodes"] = sorted(episodes, key=lambda x: x["number"])
            return data
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_episodes(self, anime_title: str) -> Dict[str, Any]:
        try:
            resp = self.session.get(
                f"{REST_API}/posts",
                params={"search": anime_title, "per_page": 100},
                timeout=30
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            posts = resp.json()
            episodes = []
            seen = set()

            for post in posts:
                title = post.get("title", {}).get("rendered", "")
                link = post.get("link", "")
                ep_match = re.search(r"Episode\s+(\d+)", title, re.I)

                if ep_match:
                    ep_num = int(ep_match.group(1))
                    if ep_num not in seen:
                        seen.add(ep_num)
                        episodes.append({"number": ep_num, "title": title, "link": link})

            episodes = sorted(episodes, key=lambda x: x["number"])
            return {"success": True, "episodes": episodes}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_episode_sources(self, episode_link: str) -> Dict[str, Any]:
        try:
            resp = self.session.get(episode_link, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            html_content = resp.text
            post_id_match = (
                re.search(r'postid-(\d+)', html_content) or 
                re.search(r'wp-json/wp/v2/posts/(\d+)', html_content)
            )

            if not post_id_match:
                return {"success": False, "error": "Post ID not found"}

            post_id = post_id_match.group(1)
            api_resp = self.session.get(f"{BASE_URL}/wp-json/hianime/v1/episode/servers/{post_id}", timeout=30)

            if api_resp.status_code != 200:
                return {"success": False, "error": f"API Status {api_resp.status_code}"}

            response_data = api_resp.json()
            html_servers = response_data.get("html", "")
            servers = []

            for match in re.finditer(r'data-server-name="([^"]+)"[^>]+data-hash="([^"]+)"', html_servers):
                name = match.group(1)
                encoded_hash = match.group(2)
                try:
                    decoded_url = base64.b64decode(encoded_hash).decode("utf-8")
                    servers.append({
                        "name": name,
                        "hash": encoded_hash,
                        "url": decoded_url,
                        "type": "sub" if "/sub" in decoded_url else "dub"
                    })
                except:
                    continue

            return {"success": True, "post_id": post_id, "servers": servers}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_stream_url(self, stream_url: str) -> Dict[str, Any]:
        try:
            resp = self.session.get(stream_url, headers={"Referer": BASE_URL}, timeout=30, allow_redirects=True)
            html_content = resp.text
            m3u8_match = re.search(r'(https?://[^\s"<>]+\.m3u8[^\s"<>]*)', html_content)

            if m3u8_match:
                return {"success": True, "m3u8_url": m3u8_match.group(1)}

            iframe_match = re.search(r'<iframe[^>]+src="([^"]+)"', html_content)
            if iframe_match:
                iframe_url = iframe_match.group(1)
                mega_resp = self.session.get(iframe_url, headers={"Referer": "https://1anime.site/"}, timeout=30)
                cid_match = re.search(r'cid\s*:\s*["\x27]([^"\x27]+)["\x27]', mega_resp.text)

                if cid_match:
                    cid = cid_match.group(1)
                    sources_resp = self.session.get(
                        f"https://megaplay.buzz/stream/getSources?id={cid}",
                        headers={"Referer": iframe_url, "X-Requested-With": "XMLHttpRequest"},
                        timeout=30
                    )
                    sources_data = sources_resp.json()
                    m3u8 = sources_data.get("sources", {}).get("file", "")
                    tracks = []
                    for track in sources_data.get("tracks", []):
                        if track.get("kind") in ["captions", "subtitles"]:
                            tracks.append({
                                "url": track.get("file", ""),
                                "lang": track.get("label", "en").lower(),
                                "label": track.get("label", "English")
                            })

                    return {"success": True, "m3u8_url": m3u8, "tracks": tracks, "type": "hls"}

            return {"success": False, "error": "No stream found"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_home(self) -> Dict[str, Any]:
        try:
            resp = self.session.get(BASE_URL, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            html_content = resp.text
            results = []
            
            # Scrape content from HTML as homepage is not JSON
            pattern = re.findall(
                r'<a href="([^"]+)" class="film-poster">.*?<img[^>]+(?:data-src|src)="([^"]+)"[^>]+alt="([^"]+)"',
                html_content,
                re.S | re.I
            )
            
            seen = set()
            for item in pattern:
                link = item[0]
                poster = html.unescape(item[1])
                title = html.unescape(item[2])

                anime_name = re.sub(r"\s+English\s+(Sub|Dub).*$", "", title).strip()

                if anime_name in seen:
                    continue

                seen.add(anime_name)
                slug = self._title_to_slug(anime_name)
                results.append({
                    "title": anime_name,
                    "slug": slug,
                    "link": link,
                    "poster": poster
                })

            return {"success": True, "anime": results}
        except Exception as e:
            return {"success": False, "error": str(e)}

# --- Flask App Initialization ---

app = Flask(__name__)
api = AniwatchAPI()

@app.route("/")
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
            "/stream?url=...": "Get m3u8 stream"
        }
    })

@app.route("/home")
def home():
    return jsonify(api.get_home())

@app.route("/search")
def search():
    keyword = request.args.get("keyword", "")
    return jsonify(api.search(keyword))

@app.route("/info/<slug>")
def info(slug):
    return jsonify(api.get_anime_info(slug))

@app.route("/episodes/<slug>")
def episodes(slug):
    return jsonify(api.get_episodes(slug))

@app.route("/sources")
def sources():
    episode_link = request.args.get("episode_link", "")
    return jsonify(api.get_episode_sources(episode_link))

@app.route("/stream")
def stream():
    url = request.args.get("url", "")
    return jsonify(api.get_stream_url(url))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
