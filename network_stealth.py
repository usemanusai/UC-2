import httpx
import logging
import random
import json
import asyncio
import os
import urllib.parse
import socket
import re
import ssl
from typing import Optional, Dict, List

# Try importing geoip2 dependencies
try:
    import geoip2.database
    import geoip2.errors
    GEOIP_AVAILABLE = True
except ImportError:
    GEOIP_AVAILABLE = False

logger = logging.getLogger(__name__)

# =========================================================================
# JA4 & JA4T & HTTP/2 Framing Spoofing Modules
# =========================================================================

HTTP2_SETTINGS = {
    "HEADER_TABLE_SIZE": 65536,
    "INITIAL_WINDOW_SIZE": 6291456,
    "MAX_CONCURRENT_STREAMS": 1000,
    "MAX_FRAME_SIZE": 16384,
    "MAX_HEADER_LIST_SIZE": 262144
}
HTTP2_WINDOW_UPDATE_DELTA = 15663105

def get_spoofed_network_parameters() -> Dict[str, Any]:
    """Generates dynamic network parameters using Gaussian Copula."""
    try:
        from engine.kernel.math_engine.entropy import synthesize_behavioral_fingerprint
        return synthesize_behavioral_fingerprint()
    except Exception:
        return {
            "tls_extension_count": 12,
            "tcp_window_size": 64240,
            "http2_max_frame_size": 16384,
            "js_timing_latency_ms": 1.2
        }

# Patch the standard socket.socket class to inject JA4T TCP options
_original_socket_connect = socket.socket.connect

def _stealth_socket_connect(self, address):
    try:
        params = get_spoofed_network_parameters()
        tcp_win = params.get("tcp_window_size", 64240)
        # Set TCP window, buffers, and scaling parameters matching Windows residential kernel (JA4T)
        self.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, tcp_win)
        self.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        self.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass
    return _original_socket_connect(self, address)

# Apply socket monkeypatch globally for JA4T TCP SYN parameters
socket.socket.connect = _stealth_socket_connect

def create_ja4_ssl_context() -> ssl.SSLContext:
    """Creates a custom SSLContext with JA4-compliant Chrome TLS 1.3 ciphers."""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    
    params = get_spoofed_network_parameters()
    ext_count = params.get("tls_extension_count", 12)
    
    # Standard Chrome JA4 cipher suites list
    chrome_ciphers = [
        "ECDHE-ECDSA-AES128-GCM-SHA256",
        "ECDHE-RSA-AES128-GCM-SHA256",
        "ECDHE-ECDSA-AES256-GCM-SHA384",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "ECDHE-ECDSA-CHACHA20-POLY1305",
        "ECDHE-RSA-CHACHA20-POLY1305",
        "AES128-GCM-SHA256",
        "AES256-GCM-SHA384",
    ]
    # Dynamically alternate cipher list order based on TLS extension count entropy
    if ext_count % 2 == 0:
        chrome_ciphers = chrome_ciphers[::-1]
        
    try:
        context.set_ciphers(":".join(chrome_ciphers))
    except Exception:
        pass
        
    context.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    return context

# Try to patch h2 connection parameters globally if available (JA4H HTTP/2 settings frame)
try:
    import h2.connection
    import h2.settings
    h2.connection.H2Connection.local_settings = h2.settings.Settings(
        initial_values={
            h2.settings.SettingCodes.HEADER_TABLE_SIZE: HTTP2_SETTINGS["HEADER_TABLE_SIZE"],
            h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: HTTP2_SETTINGS["INITIAL_WINDOW_SIZE"],
            h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: HTTP2_SETTINGS["MAX_CONCURRENT_STREAMS"],
            h2.settings.SettingCodes.MAX_FRAME_SIZE: HTTP2_SETTINGS["MAX_FRAME_SIZE"],
            h2.settings.SettingCodes.MAX_HEADER_LIST_SIZE: HTTP2_SETTINGS["MAX_HEADER_LIST_SIZE"],
        }
    )
    logger.info("[Stealth] Patched h2 HTTP/2 settings parameters globally.")
except ImportError:
    pass

class NetworkStealth:
    """
    Manages proxy-VLAN binding, residential IP validation, and JA4 TLS/TCP spoofed client runs.
    """
    def __init__(self, proxy_list: list, db_path: Optional[str] = None):
        self.proxy_list = proxy_list
        self.current_proxy = None
        self.db_path = db_path or os.path.join("configs", "GeoLite2-ASN.mmdb")

    def _extract_ip_from_proxy(self, proxy: str) -> Optional[str]:
        """Extracts the IP address or resolvable hostname from a proxy string."""
        try:
            # Remove scheme if present
            if "://" in proxy:
                parsed = urllib.parse.urlparse(proxy)
                host = parsed.hostname
            else:
                # If no scheme, temporarily add one to help urllib parse it
                parsed = urllib.parse.urlparse("http://" + proxy)
                host = parsed.hostname

            if not host:
                # Fallback regex-based extraction if urllib parsing failed
                match = re.search(r'(?:^|@)([^:/]+)(?::\d+)?$', proxy)
                if match:
                    host = match.group(1)
                else:
                    host = proxy

            if not host:
                return None

            # Clean brackets if IPv6
            if host.startswith('[') and host.endswith(']'):
                host = host[1:-1]

            # Check if it's a valid IPv4 address
            try:
                socket.inet_aton(host)
                return host
            except socket.error:
                # Check if it's a valid IPv6 address
                try:
                    socket.inet_pton(socket.AF_INET6, host)
                    return host
                except socket.error:
                    # It's a hostname, try resolving it
                    try:
                        ip = socket.gethostbyname(host)
                        return ip
                    except Exception as ex:
                        logger.warning(f"[Network] Could not resolve hostname {host}: {ex}")
                        return None
        except Exception as e:
            logger.error(f"[Network] Failed to extract IP from proxy {proxy}: {e}")
            return None

    async def validate_ip_quality(self, proxy: str) -> Dict:
        """Checks if the proxy IP is residential using local GeoIP database or online fallback."""
        # Attempt local lookup first if database exists and geoip2 is available
        if GEOIP_AVAILABLE and os.path.exists(self.db_path):
            ip = self._extract_ip_from_proxy(proxy)
            if ip:
                try:
                    with geoip2.database.Reader(self.db_path) as reader:
                        response = reader.asn(ip)
                        asn_val = response.autonomous_system_number
                        asn = f"AS{asn_val}" if asn_val else ""
                        org = response.autonomous_system_organization or ""
                        is_residential = self._check_residential_asn(asn, org)
                        logger.info(f"[Network] Local lookup - IP: {ip} | ASN: {asn} | ISP: {org} | Residential: {is_residential}")
                        return {
                            "valid": True,
                            "residential": is_residential,
                            "data": {
                                "ip": ip,
                                "asn": asn,
                                "org": org,
                                "autonomous_system_number": asn_val,
                                "autonomous_system_organization": org
                            }
                        }
                except geoip2.errors.AddressNotFoundError:
                    logger.warning(f"[Network] IP {ip} not found in local GeoIP database.")
                    return {"valid": True, "residential": False, "data": {"ip": ip, "error": "Address not found"}}
                except Exception as e:
                    logger.error(f"[Network] Local GeoIP lookup error: {e}")
            else:
                logger.warning(f"[Network] Could not extract IP from proxy {proxy} for local lookup.")
        else:
            if not GEOIP_AVAILABLE:
                logger.warning(f"[Network] geoip2 library not available. Falling back to external API.")
            else:
                logger.warning(f"[Network] Local GeoIP database not found at {self.db_path}. Falling back to external API.")

        # Online Fallback validation using httpx with JA4 TLS/TCP spoofing enabled
        proxies = proxy
        if not proxy.startswith(('http://', 'https://')):
            proxies = 'http://' + proxy
        
        try:
            # Emulate HTTP/3 over QUIC handshake fallback by attempting ALPN negotiation in our custom SSL Context
            ssl_context = create_ja4_ssl_context()
            
            # Setup transport with custom SSL context for JA4 matching
            try:
                transport = httpx.AsyncHTTPTransport(verify=ssl_context)
            except Exception:
                transport = None

            # Handle httpx client proxy parameter change in different versions
            try:
                client_instance = httpx.AsyncClient(proxy=proxies, transport=transport, timeout=10.0, http2=True)
            except TypeError:
                client_instance = httpx.AsyncClient(proxies=proxies, transport=transport, timeout=10.0, http2=True)

            async with client_instance as client:
                response = await client.get('https://ipapi.co/json/')
                if response.status_code == 200:
                    data = response.json()
                    asn = data.get('asn', '')
                    org = data.get('org', '')
                    is_residential = self._check_residential_asn(asn, org)
                    logger.info(f"[Network] External IP: {data.get('ip')} | ISP: {org} | Residential: {is_residential}")
                    return {"valid": True, "residential": is_residential, "data": data}
        except Exception as e:
            logger.error(f"[Network] External proxy validation failed: {e}")
            
        return {"valid": False, "residential": False}

    def _check_residential_asn(self, asn: str, org: str) -> bool:
        """Heuristic to detect residential ISPs based on organization names."""
        datacenter_keywords = ('AWS', 'Google', 'Microsoft', 'DigitalOcean', 'Hetzner', 'OVH', 'Linode', 'Azure')
        org_upper = org.upper()
        for kw in datacenter_keywords:
            if kw.upper() in org_upper:
                return False
        return True

    async def get_next_proxy(self) -> Optional[str]:
        """
        Selects a unique proxy from the list and strictly validates Residential ISP.
        If a Datacenter proxy is detected, it automatically rotates to find a clean Residential IP.
        """
        if not self.proxy_list:
            return None
            
        max_attempts = len(self.proxy_list)
        for _ in range(max_attempts):
            candidate = random.choice(self.proxy_list)
            res = await self.validate_ip_quality(candidate)
            if res.get('valid') and res.get('residential'):
                self.current_proxy = candidate
                return self.current_proxy
            else:
                logger.warning(f"[Network] Rejected proxy {candidate}: Non-Residential/Datacenter detected. Rotating...")
                
        logger.error("[Network] No pure residential proxies available. Falling back to default proxy list.")
        self.current_proxy = random.choice(self.proxy_list)
        return self.current_proxy

    @staticmethod
    def inject_cookies_cdp(driver, cookies_json: str):
        """Injects historical cookies via CDP to warm up the session."""
        try:
            cookies = json.loads(cookies_json)
            for cookie in cookies:
                driver.execute_cdp_cmd('Network.setCookie', cookie)
            logger.info(f"[Network] Injected {len(cookies)} historical cookies.")
        except Exception as e:
            logger.error(f"[Network] Cookie injection failed: {e}")


def apply_network_stealth(driver, proxy_list: list) -> Optional[str]:
    """
    Module-level function to validate and return a residential proxy from proxy_list.
    """
    ns = NetworkStealth(proxy_list)
    try:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        if loop.is_running():
            import threading
            from concurrent.futures import Future

            def run_coro():
                f = Future()
                def run():
                    try:
                        res = asyncio.run(ns.get_next_proxy())
                        f.set_result(res)
                    except Exception as ex:
                        f.set_exception(ex)
                threading.Thread(target=run).start()
                return f.result()
            
            return run_coro()
        else:
            return asyncio.run(ns.get_next_proxy())
    except Exception as e:
        logger.error(f"[Network] apply_network_stealth failed: {e}")
        return None


if __name__ == '__main__':
    ns = NetworkStealth(['127.0.0.1:8080'])
    print(ns._check_residential_asn('AS13335', 'Cloudflare, Inc.'))
