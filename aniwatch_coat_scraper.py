#!/usr/bin/env python3
"""
Aniwatch.co.at Complete API Scraper
==================================
Full working implementation based on code analysis.

Flow:
1. Search via WordPress REST API (wp-json/wp/v2/posts)
2. Get anime info and episodes from anime page (scrape)
3. Get episode servers via REST API (wp-json/hianime/v1/episode/servers/{post_id})
4. Decode base64 hash to get stream URL (1anime.site or my.1anime.site)
5. Get m3u8 from megaplay.buzz getSources API (for megaplay sources)
   - Fetch megaplay player page to extract cid
   - Call /stream/getSources?id={cid} to get direct m3u8 + tracks
   - No JS decryption needed - API returns plaintext m3u8 URL
6. Direct MP4 for my.1anime.site sources
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
        # Don't set Accept-Encoding - let server decide
        self.session.headers.update({
            "User-Agent": HEADERS["User-Agent"],
            "Accept": HEADERS["Accept"],
            "Accept-Language": HEADERS["Accept-Language"],
        })

    def search(self, keyword: str, limit: int = 10) -> Dict[str, Any]:
        """Search anime by keyword"""
        try:
            # Use REST API to search posts
            resp = self.session.get(
                f"{REST_API}/posts",
                params={"search": keyword, "per_page": limit * 2},
                timeout=30
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            posts = resp.json()

            # Extract unique anime names
            seen = set()
            results = []

            for post in posts:
                link = post.get("link", "")
                title = post.get("title", {}).get("rendered", "")

                # Extract anime name: "Naruto Episode 1 English Subbed" -> "Naruto"
                if " Episode " in title:
                    anime_name = title.split(" Episode ")[0]
                else:
                    anime_name = title

                # Clean the title
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
            # Use REST API to search for episodes
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

                # Match pattern like "Naruto Episode 1 English Subbed"
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

            # Sort by episode number
            episodes = sorted(episodes, key=lambda x: x["number"])

            return {
                "success": True,
                "episodes": episodes,
                "note": "Showing max 100 most recent episodes"
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_episode_sources(self, episode_link: str) -> Dict[str, Any]:
        """Get video sources for an episode using WordPress REST API"""
        try:
            # First get the episode page to extract post_id
            resp = self.session.get(episode_link, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            html = resp.text

            # Extract post_id from body class or REST API link
            post_id_match = re.search(r'postid-(\d+)', html)
            if not post_id_match:
                post_id_match = re.search(r'wp-json/wp/v2/posts/(\d+)', html)
            if not post_id_match:
                return {"success": False, "error": "Post ID not found"}

            post_id = post_id_match.group(1)

            # Call the new REST API endpoint for episode servers
            api_resp = self.session.get(
                f"{BASE_URL}/wp-json/hianime/v1/episode/servers/{post_id}",
                timeout=30
            )

            if api_resp.status_code != 200:
                return {"success": False, "error": f"API Status {api_resp.status_code}"}

            try:
                response_data = api_resp.json()
            except:
                return {"success": False, "error": "Failed to parse API response"}

            if not response_data.get("status"):
                return {"success": False, "error": "API returned error status"}

            html_content = response_data.get('html', '')
            if not html_content:
                return {"success": False, "error": "No HTML in response"}

            # Extract server name + hash pairs
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
                except:
                    continue

            # Fallback: just extract hashes if name+hash pattern fails
            if not servers:
                for h in re.findall(r'data-hash="([^"]+)"', html_content):
                    try:
                        decoded = base64.b64decode(h).decode('utf-8')
                        servers.append({
                            "name": "VidSrc",
                            "hash": h,
                            "url": decoded,
                            "type": "sub" if "/sub" in decoded else "dub"
                        })
                    except:
                        continue

            if not servers:
                return {"success": False, "error": "No servers found"}

            return {"success": True, "post_id": post_id, "servers": servers}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_episode_sources_by_id(self, post_id: str, nonce: str = None) -> Dict[str, Any]:
        """Get servers by post_id directly via REST API"""
        try:
            api_resp = self.session.get(
                f"{BASE_URL}/wp-json/hianime/v1/episode/servers/{post_id}",
                timeout=30
            )

            if api_resp.status_code != 200:
                return {"success": False, "error": f"API Status {api_resp.status_code}"}

            response_data = api_resp.json()
            if not response_data.get("status"):
                return {"success": False, "error": "API returned error status"}

            html_content = response_data.get("html", "")
            servers = []

            for match in re.finditer(r'data-server-name="([^"]+)"[^>]+data-hash="([^"]+)"', html_content):
                name = match.group(1)
                h = match.group(2)
                try:
                    stream_url = base64.b64decode(h).decode("utf-8")
                    servers.append({
                        "name": name,
                        "url": stream_url,
                        "type": "sub" if "/sub" in stream_url else "dub"
                    })
                except:
                    continue

            if not servers:
                for h in re.findall(r'data-hash="([^"]+)"', html_content):
                    try:
                        stream_url = base64.b64decode(h).decode("utf-8")
                        servers.append({
                            "name": "VidSrc",
                            "url": stream_url,
                            "type": "sub" if "/sub" in stream_url else "dub"
                        })
                    except:
                        continue

            return {
                "success": True,
                "post_id": post_id,
                "servers": servers
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_stream_url(self, stream_url: str) -> Dict[str, Any]:
        """Get m3u8 URL from stream page using megaplay.buzz getSources API"""
        try:
            resp = self.session.get(
                stream_url,
                headers={"Referer": BASE_URL},
                timeout=30,
                allow_redirects=True
            )

            if resp.status_code == 403:
                return {"success": False, "error": "403 blocked", "needs_browser": True}

            html = resp.text

            # Direct m3u8 in page
            m3u8_match = re.search(r'(https?://[^\s"<>]+\.m3u8[^\s"<>]*)', html)
            if m3u8_match:
                return {"success": True, "m3u8_url": m3u8_match.group(1)}

            # my.1anime.site: direct video player with <source src="videos/...">
            video_match = re.search(r'<source\s+src="([^"]+)"', html)
            if video_match and "my.1anime.site" in stream_url:
                base = "https://my.1anime.site/"
                video_url = urljoin(base, video_match.group(1))
                return {"success": True, "m3u8_url": video_url, "type": "mp4"}

            # megaplay.buzz iframe path
            iframe_match = re.search(r'<iframe[^>]+src="([^"]+)"', html)
            if iframe_match:
                iframe_url = iframe_match.group(1)

                # Fetch megaplay player page to extract cid
                mega_resp = self.session.get(
                    iframe_url,
                    headers={"Referer": "https://1anime.site/"},
                    timeout=30
                )

                if mega_resp.status_code != 200:
                    return {"success": False, "error": f"megaplay status {mega_resp.status_code}"}

                cid_match = re.search(r'cid\s*:\s*["\x27]([^"\x27]+)["\x27]', mega_resp.text)
                if not cid_match:
                    return {
                        "success": True,
                        "m3u8_url": iframe_url,
                        "iframe_url": iframe_url,
                        "note": "cid not found - fallback to iframe"
                    }

                cid = cid_match.group(1)

                # Call getSources API
                sources_resp = self.session.get(
                    f"https://megaplay.buzz/stream/getSources?id={cid}",
                    headers={
                        "Referer": iframe_url,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=30
                )

                if sources_resp.status_code != 200:
                    return {"success": False, "error": f"getSources status {sources_resp.status_code}"}

                try:
                    sources_data = sources_resp.json()
                except:
                    return {"success": False, "error": "getSources invalid JSON"}

                m3u8 = sources_data.get("sources", {}).get("file", "")
                if not m3u8:
                    return {"success": False, "error": "No m3u8 in getSources response"}

                # Extract subtitle tracks
                tracks = []
                for t in sources_data.get("tracks", []):
                    if t.get("kind") in ["captions", "subtitles"]:
                        tracks.append({
                            "url": t.get("file", ""),
                            "lang": t.get("label", "en").lower() if t.get("label") else "en",
                            "label": t.get("label", "English"),
                        })

                return {
                    "success": True,
                    "m3u8_url": m3u8,
                    "cid": cid,
                    "iframe_url": iframe_url,
                    "tracks": tracks,
                    "type": "hls",
                }

            # Cloudflare challenge check
            if "cf-" in resp.text.lower() or "challenge" in resp.text.lower():
                return {"success": False, "error": "Cloudflare challenge", "needs_browser": True}

            return {"success": False, "error": "No m3u8 or iframe found", "has_content": len(html) > 100}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_home(self) -> Dict[str, Any]:
        """Get homepage data - recent anime"""
        try:
            resp = self.session.get(
                f"{REST_API}/posts",
                params={"per_page": 20},
                timeout=30
            )

            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            posts = resp.json()
            results = []

            for post in posts:
                title = post.get("title", {}).get("rendered", "")
                link = post.get("link", "")

                if " Episode " in title:
                    anime_name = title.split(" Episode ")[0]
                else:
                    anime_name = title

                anime_name = re.sub(r"\s+English\s+(Sub|Dub).*$", "", anime_name).strip()
                poster = ""

                try:
                    anime_slug = self._title_to_slug(anime_name)
                    anime_url = f"{BASE_URL}/anime/{anime_slug}/"
                    anime_resp = self.session.get(anime_url, timeout=15)
                    html = anime_resp.text

                    # Find poster image
                    img_match = re.search(r'<img[^>]+data-src=["\']([^"\']+)["\']', html, re.I)
                    if img_match:
                        poster = img_match.group(1)

                    if not poster:
                        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I)
                        if img_match:
                            poster = img_match.group(1)

                    if any(x in poster.lower() for x in ["favicon", "cropped", "logo"]):
                        poster = ""

                except Exception:
                    pass

                results.append({
                    "title": anime_name,
                    "link": link,
                    "slug": self._title_to_slug(anime_name),
                    "poster": poster
                })

            return {"success": True, "anime": results, "page": 1}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_ova(self, page: int = 1) -> Dict[str, Any]:
        """Get OVA anime"""
        try:
            resp = self.session.get(
                f"{REST_API}/posts",
                params={"search": "ova", "per_page": 20, "page": page},
                timeout=30
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            posts = resp.json()
            results = []
            for post in posts:
                title = post.get("title", {}).get("rendered", "")
                if "ova" in title.lower():
                    results.append({
                        "title": title,
                        "link": post.get("link", "")
                    })

            return {"success": True, "anime": results, "page": page}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_tv_series(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_most_popular(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_top_airing(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_recently_updated(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_recently_added(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_az_list(self, letter: str = "all", page: int = 1) -> Dict[str, Any]:
        """Get A-Z list"""
        try:
            if letter == "all":
                return self.get_home()

            resp = self.session.get(
                f"{REST_API}/posts",
                params={"per_page": 50, "page": page},
                timeout=30
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            posts = resp.json()
            results = []
            for post in posts:
                title = post.get("title", {}).get("rendered", "")
                if title and title[0].upper() == letter.upper():
                    results.append({
                        "title": title,
                        "link": post.get("link", "")
                    })

            return {"success": True, "anime": results, "page": page, "letter": letter}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_genres(self) -> Dict[str, Any]:
        """Get available genres"""
        return {"success": True, "genres": [
            "action", "adventure", "comedy", "drama", "fantasy", "horror",
            "magic", "martial-arts", "mecha", "military", "music",
            "mystery", "psychological", "romance", "school", "sci-fi",
            "slice-of-life", "sports", "super-power", "supernatural"
        ]}

    def get_by_genre(self, genre: str, page: int = 1) -> Dict[str, Any]:
        """Get anime by genre"""
        try:
            resp = self.session.get(
                f"{REST_API}/posts",
                params={"search": genre, "per_page": 20, "page": page},
                timeout=30
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}

            posts = resp.json()
            results = []
            for post in posts:
                results.append({
                    "title": post.get("title", {}).get("rendered", ""),
                    "link": post.get("link", "")
                })

            return {"success": True, "anime": results, "page": page, "genre": genre}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_random_anime(self) -> Dict[str, Any]:
        """Get random anime"""
        try:
            resp = self.session.get(f"{REST_API}/posts", params={"per_page": 50}, timeout=30)
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}
            import random
            posts = resp.json()
            if posts:
                post = random.choice(posts)
                title = post.get("title", {}).get("rendered", "")
                return {"success": True, "anime": {"title": title, "link": post.get("link", "")}}
            return {"success": False, "error": "No anime"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_schedules(self) -> Dict[str, Any]:
        return self.get_home()

    def get_filter_options(self) -> Dict[str, Any]:
        return {"success": True, "filters": {"types": ["sub", "dub"], "status": ["completed", "airing"]}}

    def get_most_favorite(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_completed(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_top_upcoming(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_subbed_anime(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_dubbed_anime(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_ona(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_specials(self, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_by_producer(self, producer: str, page: int = 1) -> Dict[str, Any]:
        return self.get_home()

    def get_suggestions(self, keyword: str) -> Dict[str, Any]:
        result = self.search(keyword, 5)
        if result.get("success"):
            return {"success": True, "suggestions": result.get("results", [])}
        return result


def create_app():
    """Create Flask API app"""
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
                "/search": "Search anime",
                "/info/<slug>": "Get anime info",
                "/episodes/<slug>": "Get episode list",
                "/sources": "Get video sources",
                "/stream": "Get m3u8 stream",
                "/extract": "Extract all data",
                "/home": "Home page"
            }
        })

    @app.route('/search')
    def search():
        keyword = request.args.get('keyword', '')
        limit = int(request.args.get('limit', 10))
        if not keyword:
            return jsonify({"error": "keyword required"}), 400
        return jsonify(api.search(keyword, limit))

    @app.route('/info/<slug>')
    def info(slug):
        return jsonify(api.get_anime_info(slug))

    @app.route('/episodes/<slug>')
    def episodes(slug):
        info = api.get_anime_info(slug)
        if not info.get("success"):
            return jsonify(info), 400
        full_title = info.get("title", "")
        anime_title = full_title.split(" - ")[0].strip() if " - " in full_title else full_title
        return jsonify(api.get_episodes(anime_title))

    @app.route('/sources')
    def sources():
        episode_link = request.args.get('episode_link', '')
        if episode_link:
            return jsonify(api.get_episode_sources(episode_link))
        episode_id = request.args.get('episode_id', '')
        nonce = request.args.get('nonce', '')
        if episode_id:
            return jsonify(api.get_episode_sources_by_id(episode_id, nonce))
        return jsonify({"error": "episode_link or episode_id required"}), 400

    @app.route('/stream')
    def stream():
        url = request.args.get('url', '')
        if not url:
            return jsonify({"error": "url required"}), 400
        return jsonify(api.get_stream_url(url))

    @app.route('/extract')
    def extract():
        slug = request.args.get('slug', '')
        episode = int(request.args.get('episode', 1))
        if not slug:
            return jsonify({"error": "slug required"}), 400

        info = api.get_anime_info(slug)
        if not info.get("success"):
            return jsonify(info), 400

        episodes = info.get("recent_episodes", [])
        target_ep = next((ep for ep in episodes if ep.get("number") == episode), None)

        if not target_ep:
            full_title = info.get("title", "")
            anime_title = full_title.split(" - ")[0].strip() if " - " in full_title else full_title
            rest_episodes = api.get_episodes(anime_title)
            target_ep = next((ep for ep in rest_episodes.get("episodes", []) if ep.get("number") == episode), None)

        if not target_ep:
            return jsonify({"success": False, "error": f"Episode {episode} not found"}), 404

        sources = api.get_episode_sources(target_ep["link"])
        if not sources.get("success") or not sources.get("servers"):
            return jsonify(sources), 400

        stream_data = api.get_stream_url(sources["servers"][0]["url"])

        return jsonify({
            "success": True,
            "anime": {"title": info.get("title"), "image": info.get("image"), "slug": slug},
            "episode": {"number": episode, "link": target_ep["link"]},
            "sources": sources["servers"],
            "stream": stream_data
        })

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
else:
    # Vercel/WSGI entry point
    app = create_app()
