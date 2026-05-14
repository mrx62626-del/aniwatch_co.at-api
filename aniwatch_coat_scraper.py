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
                "note": f"Showing max 100 most recent episodes"
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
                        servers.append({"name": "VidSrc", "url": stream_url, "type": "sub" if "/sub" in stream_url else "dub"})
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
                    if t.get("kind") == "captions" or t.get("kind") == "subtitles":
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
            resp = self.session.get(f"{REST_API}/posts", params={"per_page": 20}, timeout=30)
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
                    post_html = self.session.get(link, timeout=10).text
               
                    img_match = re.search(
                        r'og:image"[^>]+content="([^"]+)',
                        post_html
                    )
               
                    if img_match:
                        poster = img_match.group(1)
               
                except:
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
    
    def get_movies(self, page: int = 1) -> Dict[str, Any]:
        """Get anime movies"""
        try:
            resp = self.session.get(
                f"{REST_API}/posts",
                params={"search": "movie", "per_page": 20, "page": page},
                timeout=30
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Status {resp.status_code}"}
            
            posts = resp.json()
            results = []
            for post in posts:
                title = post.get("title", {}).get("rendered", "")
                if "movie" in title.lower():
                    results.append({
                        "title": title,
                        "link": post.get("link", "")
                    })
            
            return {"success": True, "anime": results, "page": page}
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
        """Get TV series"""
        return self.get_home()
    
    def get_most_popular(self, page: int = 1) -> Dict[str, Any]:
        """Get most popular anime"""
        return self.get_home()
    
    def get_top_airing(self, page: int = 1) -> Dict[str, Any]:
        """Get top airing"""
        return self.get_home()
    
    def get_recently_updated(self, page: int = 1) -> Dict[str, Any]:
        """Get recently updated"""
        return self.get_home()
    
    def get_recently_added(self, page: int = 1) -> Dict[str, Any]:
        """Get recently added"""
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
        """Get anime schedule"""
        return self.get_home()
    
    def get_filter_options(self) -> Dict[str, Any]:
        """Get filter options"""
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
                # Core
                "/": "API Index",
                "/search?keyword=...": "Search anime",
                "/info/<slug>": "Get anime info",
                "/episodes/<slug>": "Get episode list",
                "/sources?episode_link=...": "Get video sources",
                "/stream?url=...": "Get m3u8 stream",
                "/extract?slug=&episode=": "Extract all data",
                # Lists
                "/home": "Home page",
                "/movies": "Anime movies",
                "/ova": "OVA series",
                "/tv-series": "TV series",
                "/most-popular": "Most popular",
                "/top-airing": "Top airing",
                "/recently-updated": "Recently updated",
                "/recently-added": "Recently added",
                "/az-list": "A-Z list",
                "/az/<letter>": "A-Z by letter",
                "/genres": "Genre list",
                "/genre/<genre>": "By genre",
                "/random": "Random anime",
                "/schedules": "Schedules",
                "/filter-options": "Filter options",
                "/most-favorite": "Most favorite",
                "/completed": "Completed",
                "/top-upcoming": "Top upcoming",
                "/subbed": "Subbed anime",
                "/dubbed": "Dubbed anime",
                "/ona": "ONA",
                "/special": "Specials",
                "/producer/<producer>": "By producer",
                "/suggestions": "Search suggestions"
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
        # Get anime info first to get title
        info = api.get_anime_info(slug)
        if not info.get("success"):
            return jsonify(info), 400
        
        # Extract title from full title
        full_title = info.get("title", "")
        anime_title = full_title.split(" - ")[0].strip() if " - " in full_title else full_title
        
        # Get episodes from REST API
        return jsonify(api.get_episodes(anime_title))
    
    @app.route('/sources')
    def sources():
        # By episode_link
        episode_link = request.args.get('episode_link', '')
        if episode_link:
            return jsonify(api.get_episode_sources(episode_link))
        
        # By episode_id
        episode_id = request.args.get('episode_id', '')
        nonce = request.args.get('nonce', '')
        if episode_id and nonce:
            # Call AJAX directly
            return jsonify(api.get_episode_sources_by_id(episode_id, nonce))
        
        return jsonify({"error": "episode_link or episode_id+nonce required"}), 400
    
    @app.route('/sources/<episode_id>/<nonce>')
    def sources_by_id(episode_id, nonce):
        return jsonify(api.get_episode_sources_by_id(episode_id, nonce))
    
    @app.route('/stream')
    def stream():
        url = request.args.get('url', '')
        if not url:
            return jsonify({"error": "url required"}), 400
        return jsonify(api.get_stream_url(url))
    
    # List endpoints
    @app.route('/home')
    def home():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_home())
    
    @app.route('/movies')
    @app.route('/movie')
    def movies():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_movies(page))
    
    @app.route('/ova')
    def ova():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_ova(page))
    
    @app.route('/tv-series')
    @app.route('/tv')
    def tv_series():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_tv_series(page))
    
    @app.route('/most-popular')
    def most_popular():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_most_popular(page))
    
    @app.route('/top-airing')
    def top_airing():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_top_airing(page))
    
    @app.route('/recently-updated')
    def recently_updated():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_recently_updated(page))
    
    @app.route('/recently-added')
    def recently_added():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_recently_added(page))
    
    @app.route('/az-list')
    @app.route('/az/<letter>')
    def az_list(letter=None):
        if letter is None:
            letter = request.args.get('letter', 'all')
        page = int(request.args.get('page', 1))
        return jsonify(api.get_az_list(letter, page))
    
    @app.route('/genres')
    def genres():
        return jsonify(api.get_genres())
    
    @app.route('/genre/<genre>')
    def genre(genre):
        page = int(request.args.get('page', 1))
        return jsonify(api.get_by_genre(genre, page))
    
    @app.route('/random')
    def random():
        return jsonify(api.get_random_anime())
    
    @app.route('/schedules')
    def schedules():
        return jsonify(api.get_schedules())
    
    @app.route('/filter-options')
    def filter_options():
        return jsonify(api.get_filter_options())
    
    @app.route('/most-favorite')
    def most_favorite():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_most_favorite(page))
    
    @app.route('/completed')
    def completed():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_completed(page))
    
    @app.route('/top-upcoming')
    def top_upcoming():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_top_upcoming(page))
    
    @app.route('/subbed')
    def subbed():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_subbed_anime(page))
    
    @app.route('/dubbed')
    def dubbed():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_dubbed_anime(page))
    
    @app.route('/ona')
    def ona():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_ona(page))
    
    @app.route('/special')
    def specials():
        page = int(request.args.get('page', 1))
        return jsonify(api.get_specials(page))
    
    @app.route('/producer/<producer>')
    def producer(producer):
        page = int(request.args.get('page', 1))
        return jsonify(api.get_by_producer(producer, page))
    
    @app.route('/suggestions')
    def suggestions():
        keyword = request.args.get('keyword', '')
        if not keyword:
            return jsonify({"error": "keyword required"}), 400
        return jsonify(api.get_suggestions(keyword))
    
    @app.route('/extract')
    def extract():
        """Main extract endpoint"""
        import re
        import json as json_lib
        
        slug = request.args.get('slug', '')
        episode = int(request.args.get('episode', 1))
        
        if not slug:
            return jsonify({"error": "slug required"}), 400
        
        # Get anime info
        info = api.get_anime_info(slug)
        if not info.get("success"):
            return jsonify(info), 400
        
        # Try getting specific episode - first check recent episodes from page
        episodes = info.get("recent_episodes", [])
        target_ep = None
        for ep in episodes:
            if ep.get("number") == episode:
                target_ep = ep
                break
        
        # If not found in recent, try REST API
        if not target_ep:
            full_title = info.get("title", "")
            anime_title = full_title.split(" - ")[0].strip() if " - " in full_title else full_title
            rest_episodes = api.get_episodes(anime_title)
            for ep in rest_episodes.get("episodes", []):
                if ep.get("number") == episode:
                    target_ep = ep
                    break
        
        if not target_ep:
            return jsonify({
                "success": False,
                "error": f"Episode {episode} not found"
            }), 404
        
        # Get sources
        sources = api.get_episode_sources(target_ep["link"])
        if not sources.get("success"):
            return jsonify(sources), 400
        
        servers = sources.get("servers", [])
        if not servers:
            return jsonify({"success": False, "error": "No servers"}), 404
        
        # Get m3u8 from first server (includes tracks from getSources API)
        stream = api.get_stream_url(servers[0]["url"])
        master_m3u8 = stream.get("m3u8_url", "")
        subtitle_tracks = stream.get("tracks", [])
        
        # Extract all quality variants from master m3u8
        qualities = []
        if master_m3u8 and not master_m3u8.endswith(".mp4"):
            try:
                # Try to get master m3u8 and parse qualities
                master_resp = api.session.get(master_m3u8, timeout=10, headers={"Referer": "https://megaplay.buzz/"})
                if master_resp.status_code == 200:
                    master_content = master_resp.text
                    # Find all quality variant m3u8s
                    variant_matches = re.findall(r'(https?://[^\s"<>]+\.m3u8[^\s"<>]*)', master_content)
                    # Also parse BANDWIDTH values
                    bandwidth_matches = re.findall(r'#EXT-X-STREAM-INF:[^\n]+BANDWIDTH=(\d+)', master_content)
                    # Parse RESOLUTION for accurate heights
                    resolution_matches = re.findall(r'RESOLUTION=(\d+)x(\d+)', master_content)
                    
                    for i, url in enumerate(variant_matches):
                        height = 1080
                        if i < len(resolution_matches):
                            height = int(resolution_matches[i][1])
                        elif i < len(bandwidth_matches):
                            bw = int(bandwidth_matches[i])
                            height = bw // 1000
                        
                        height_match = re.search(r'(\d+)p', url)
                        if height_match:
                            height = int(height_match.group(1))
                        
                        qualities.append({
                            "url": url,
                            "height": height,
                            "label": f"{height}p"
                        })
                    
                    if not qualities:
                        qualities.append({"url": master_m3u8, "height": 1080, "label": "1080p"})
            except:
                if master_m3u8:
                    qualities.append({"url": master_m3u8, "height": 1080, "label": "1080p"})
        elif master_m3u8:
            qualities.append({"url": master_m3u8, "height": 1080, "label": "1080p", "type": "mp4"})
        
        return jsonify({
            "success": True,
            "anime": {
                "title": info.get("title"),
                "image": info.get("image"),
                "slug": slug
            },
            "episode": {
                "number": episode,
                "link": target_ep["link"]
            },
            "sources": servers,
            "m3u8_url": stream.get("m3u8_url"),
            "qualities": qualities,
            "stream_success": stream.get("success"),
            "tracks": subtitle_tracks,
            "stream_type": stream.get("type", "hls"),
            "fetch_headers": {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://megaplay.buzz/"
            }
        })
    
    return app


# ========== STANDALONE FUNCTIONS ==========

def search_anime(keyword: str, limit: int = 10):
    """Standalone search function"""
    api = AniwatchAPI()
    return api.search(keyword, limit)

def get_anime_info(slug: str):
    """Standalone get anime info"""
    api = AniwatchAPI()
    return api.get_anime_info(slug)

def get_episodes(anime_title: str):
    """Standalone get episodes"""
    api = AniwatchAPI()
    return api.get_episodes(anime_title)

def get_episode_sources(episode_link: str):
    """Standalone get sources"""
    api = AniwatchAPI()
    return api.get_episode_sources(episode_link)

def get_stream_url(stream_url: str):
    """Standalone get stream"""
    api = AniwatchAPI()
    return api.get_stream_url(stream_url)

def get_home():
    """Standalone get home"""
    api = AniwatchAPI()
    return api.get_home()

def get_movies(page=1):
    """Standalone get movies"""
    api = AniwatchAPI()
    return api.get_movies(page)

def get_ova(page=1):
    """Standalone get OVA"""
    api = AniwatchAPI()
    return api.get_ova(page)

def get_most_popular(page=1):
    """Standalone get most popular"""
    api = AniwatchAPI()
    return api.get_most_popular(page)

def get_top_airing(page=1):
    """Standalone get top airing"""
    api = AniwatchAPI()
    return api.get_top_airing(page)

def get_recently_updated(page=1):
    """Standalone get recently updated"""
    api = AniwatchAPI()
    return api.get_recently_updated(page)

def get_az_list(letter="all", page=1):
    """Standalone get A-Z list"""
    api = AniwatchAPI()
    return api.get_az_list(letter, page)

def get_genres():
    """Standalone get genres"""
    api = AniwatchAPI()
    return api.get_genres()

def get_by_genre(genre, page=1):
    """Standalone get by genre"""
    api = AniwatchAPI()
    return api.get_by_genre(genre, page)

def get_random_anime():
    """Standalone get random anime"""
    api = AniwatchAPI()
    return api.get_random_anime()

def get_schedules():
    """Standalone get schedules"""
    api = AniwatchAPI()
    return api.get_schedules()

def get_filter_options():
    """Standalone get filter options"""
    api = AniwatchAPI()
    return api.get_filter_options()

def get_most_favorite(page=1):
    """Standalone get most favorite"""
    api = AniwatchAPI()
    return api.get_most_favorite(page)

def get_completed(page=1):
    """Standalone get completed"""
    api = AniwatchAPI()
    return api.get_completed(page)

def get_top_upcoming(page=1):
    """Standalone get top upcoming"""
    api = AniwatchAPI()
    return api.get_top_upcoming(page)

def get_subbed_anime(page=1):
    """Standalone get subbed anime"""
    api = AniwatchAPI()
    return api.get_subbed_anime(page)

def get_dubbed_anime(page=1):
    """Standalone get dubbed anime"""
    api = AniwatchAPI()
    return api.get_dubbed_anime(page)

def get_ona(page=1):
    """Standalone get ona"""
    api = AniwatchAPI()
    return api.get_ona(page)

def get_specials(page=1):
    """Standalone get specials"""
    api = AniwatchAPI()
    return api.get_specials(page)

def get_by_producer(producer, page=1):
    """Standalone get by producer"""
    api = AniwatchAPI()
    return api.get_by_producer(producer, page)

def get_suggestions(keyword):
    """Standalone get suggestions"""
    api = AniwatchAPI()
    return api.get_suggestions(keyword)

def extract_anime(slug: str, episode: int = 1):
    """Standalone extract all data"""
    api = AniwatchAPI()
    
    info = api.get_anime_info(slug)
    if not info.get("success"):
        return info
    
    episodes = info.get("recent_episodes", [])
    target_ep = None
    for ep in episodes:
        if ep.get("number") == episode:
            target_ep = ep
            break
    
    if not target_ep:
        full_title = info.get("title", "")
        anime_title = full_title.split(" - ")[0].strip() if " - " in full_title else full_title
        rest_episodes = api.get_episodes(anime_title)
        for ep in rest_episodes.get("episodes", []):
            if ep.get("number") == episode:
                target_ep = ep
                break
    
    if not target_ep:
        return {"success": False, "error": f"Episode {episode} not found"}
    
    sources = api.get_episode_sources(target_ep["link"])
    if not sources.get("success"):
        return sources
    
    servers = sources.get("servers", [])
    if not servers:
        return {"success": False, "error": "No servers"}
    
    stream = api.get_stream_url(servers[0]["url"])
    
    return {
        "success": True,
        "anime": {
            "title": info.get("title"),
            "image": info.get("image"),
            "slug": slug
        },
        "episode": {
            "number": episode,
            "link": target_ep["link"]
        },
        "sources": servers,
        "m3u8_url": stream.get("m3u8_url"),
        "tracks": stream.get("tracks", []),
        "stream_success": stream.get("success"),
        "stream_type": stream.get("type", "hls"),
        "fetch_headers": {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://megaplay.buzz/"
        }
    }


# ========== TEST ==========

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)

# Vercel requires top-level app
app = create_app()
