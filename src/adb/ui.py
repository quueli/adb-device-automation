import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from src.adb.shell import AdbShell, log


class UiTree(AdbShell):
    def __init__(self, device_id: str = None, xml_cache_ttl: float = 0.3):
        super().__init__(device_id)
        # several find/wait checks in a row look at the same "moment" of the screen
        # and a real dump costs 2-3s on some phones, so the last one is reused for
        # xml_cache_ttl and dropped by anything that can change the screen
        self._xml_cache: Optional[str] = None
        self._xml_cache_time = 0.0
        self._xml_cache_ttl = xml_cache_ttl
        self._u2 = None
        self._u2_failed = False

    def _get_u2(self):
        # uiautomator2 keeps a server on the device: dump_hierarchy ~0.2s against
        # ~2.7s for `uiautomator dump`. any failure falls back to the plain dump
        if self._u2 is not None:
            return self._u2
        if self._u2_failed or os.getenv('U2_ENABLED', '1').lower() not in ('1', 'true', 'yes'):
            return None
        try:
            import uiautomator2 as u2
            self._u2 = u2.connect(self.device_id)
            return self._u2
        except Exception as e:
            log.info(f"[{self.device_id}] uiautomator2 not available ({type(e).__name__}: {e}), "
                     f"using uiautomator dump")
            self._u2_failed = True
            return None

    def invalidate_xml_cache(self):
        self._xml_cache = None

    def _store_xml(self, xml: str) -> str:
        self._xml_cache = xml
        self._xml_cache_time = time.time()
        return xml

    def get_screen_text(self, use_cache: bool = True) -> str:
        """Raw uiautomator xml of the current screen, '' if every dump path failed."""
        if use_cache and self._xml_cache is not None and (time.time() - self._xml_cache_time) < self._xml_cache_ttl:
            return self._xml_cache

        d = self._get_u2()
        if d is not None:
            try:
                xml = d.dump_hierarchy()
                if xml and '<hierarchy' in xml:
                    return self._store_xml(xml)
            except Exception:
                pass

        # one adb round trip: dump straight to the tty instead of file + cat
        try:
            r = self._adb('exec-out', 'uiautomator', 'dump', '/dev/tty', timeout=15)
            xml = r.stdout or ''
            if '<hierarchy' in xml:
                start = xml.find('<?xml')
                if start < 0:
                    start = xml.find('<hierarchy')
                end = xml.rfind('</hierarchy>')
                xml = xml[start:end + len('</hierarchy>')] if end >= 0 else xml[start:]
                return self._store_xml(xml)
        except Exception:
            pass

        # slowest path, for devices where exec-out mangles the output
        try:
            self.execute_command('uiautomator dump /sdcard/window_dump.xml')
            xml = self.execute_command('cat /sdcard/window_dump.xml')
            if xml and '<hierarchy' in xml:
                return self._store_xml(xml)
        except Exception:
            pass
        return ''

    def get_screen_xml(self):
        xml_str = self.get_screen_text()
        if not xml_str:
            return None
        try:
            return ET.fromstring(xml_str)
        except ET.ParseError:
            return None

    @staticmethod
    def _bounds_center(bounds: str) -> Optional[tuple]:
        # bounds look like [x1,y1][x2,y2]
        try:
            x1, y1, x2, y2 = [int(n) for n in re.findall(r'\d+', bounds)]
            return (x1 + x2) // 2, (y1 + y2) // 2
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _match_text(node: Any, text: str, partial: bool) -> bool:
        t = (node.attrib.get('text') or '').lower()
        d = (node.attrib.get('content-desc') or '').lower()
        text_lower = text.lower()
        match_text = (text_lower in t) if partial else (t == text_lower)
        match_desc = (text_lower in d) if partial else (d == text_lower)
        return match_text or match_desc

    @staticmethod
    def _match_resource(node: Any, resource_id: str) -> bool:
        rid = node.attrib.get('resource-id') or ''
        return rid.endswith(resource_id) or rid == resource_id

    @staticmethod
    def _match_class(node: Any, class_name: str) -> bool:
        cls = node.attrib.get('class') or ''
        return cls.endswith(class_name) or cls == class_name

    @staticmethod
    def _match_content_desc(node: Any, content_desc: str, partial: bool) -> bool:
        d = (node.attrib.get('content-desc') or '').lower()
        cd_lower = content_desc.lower()
        return (cd_lower in d) if partial else (d == cd_lower)

    def _matches(self, node, text, partial_text, resource_id, content_desc, partial_desc, class_name) -> bool:
        if text and not self._match_text(node, text, partial_text):
            return False
        if resource_id and not self._match_resource(node, resource_id):
            return False
        if content_desc and not self._match_content_desc(node, content_desc, partial_desc):
            return False
        if class_name and not self._match_class(node, class_name):
            return False
        return True

    def find_element(
        self,
        text: str = None,
        partial_text: bool = True,
        resource_id: str = None,
        content_desc: str = None,
        partial_desc: bool = True,
        class_name: str = None,
    ) -> Optional[Dict[str, Any]]:
        for hit in self._iter_matches(text, partial_text, resource_id, content_desc, partial_desc, class_name):
            return hit
        return None

    def find_all_elements(
        self,
        text: str = None,
        partial_text: bool = True,
        resource_id: str = None,
        content_desc: str = None,
        partial_desc: bool = True,
        class_name: str = None,
    ) -> List[Dict[str, Any]]:
        # document order; sort by center x or y yourself when the layout matters
        return list(self._iter_matches(text, partial_text, resource_id, content_desc, partial_desc, class_name))

    def _iter_matches(self, text, partial_text, resource_id, content_desc, partial_desc, class_name):
        root = self.get_screen_xml()
        if root is None:
            return
        for node in root.iter():
            if not self._matches(node, text, partial_text, resource_id, content_desc, partial_desc, class_name):
                continue
            bounds = node.attrib.get('bounds')
            center = self._bounds_center(bounds) if bounds else None
            if center:
                yield {'center': center, 'node': node}
