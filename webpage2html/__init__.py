from __future__ import annotations
import argparse
import asyncio
import base64
import codecs
import datetime
import os
import re
import sys
import logging

from bs4 import BeautifulSoup

from urllib.parse import urlparse, urlunsplit, urljoin, quote

re_css_url = re.compile(r"(url\(.*?\))")
webpage2html_cache = {}

import httpx


def absurl(index, relpath=None, normpath=None):
    if relpath is None:
        if isinstance(index, bytes):
            return index.decode("utf-8")
        return str(index)

    if isinstance(index, bytes):
        index = index.decode("utf-8")
    if isinstance(relpath, bytes):
        relpath = relpath.decode("utf-8")

    index = str(index)
    relpath = str(relpath)

    return urljoin(index, relpath)


class Agent:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.errors = []

    async def get(
        self,
        index,
        relpath=None,
        username=None,
        password=None,
    ):
        global webpage2html_cache
        full_path = absurl(index, relpath)
        if not full_path:
            logging.warning("invalid path %s %s" % (index, relpath))
            return "", None

        if isinstance(full_path, bytes):
            full_path = full_path.decode("utf-8")
        full_path = str(full_path)
        full_path = quote(full_path, safe="%/:=&?~#+!$,;'@()*[]")

        if full_path in webpage2html_cache:
            logging.info("mem cache hit: - %s" % full_path)
            return webpage2html_cache[full_path], None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:65.0) Gecko/20100101 Firefox/65.0"
        }

        auth = None
        if username and password:
            auth = (username, password)

        try:
            logging.info("GET %s" % (full_path))
            response = await self.client.get(
                full_path, headers=headers, auth=auth, follow_redirects=True
            )
            response.raise_for_status()

            if response.headers.get("content-type", "").lower().startswith("text/"):
                content = response.text
            else:
                content = response.content
            webpage2html_cache[response.url] = content
            return content, {
                "url": response.url,
                "content-type": response.headers.get("content-type"),
            }
        except Exception as e:
            err = "Failed to fetch %s: %s" % (full_path, str(e))
            logging.error(err)
            self.errors.append(err)
            return "", None

    async def data_to_base64(self, index, src):
        # doc here: http://en.wikipedia.org/wiki/Data_URI_scheme
        if not src:
            return src
        sp = urlparse(src).path.lower()
        if src.strip().startswith("data:"):
            return src
        if sp.endswith(".png"):
            fmt = "image/png"
        elif sp.endswith(".gif"):
            fmt = "image/gif"
        elif sp.endswith(".ico"):
            fmt = "image/x-icon"
        elif sp.endswith(".jpg") or sp.endswith(".jpeg"):
            fmt = "image/jpg"
        elif sp.endswith(".svg"):
            fmt = "image/svg+xml"
        elif sp.endswith(".ttf"):
            fmt = "application/x-font-ttf"
        elif sp.endswith(".otf"):
            fmt = "application/x-font-opentype"
        elif sp.endswith(".woff"):
            fmt = "application/font-woff"
        elif sp.endswith(".woff2"):
            fmt = "application/font-woff2"
        elif sp.endswith(".eot"):
            fmt = "application/vnd.ms-fontobject"
        elif sp.endswith(".sfnt"):
            fmt = "application/font-sfnt"
        elif sp.endswith(".css") or sp.endswith(".less"):
            fmt = "text/css"
        elif sp.endswith(".js"):
            fmt = "application/javascript"
        else:
            # what if it's not a valid font type? may not matter
            fmt = "image/png"
        data, extra_data = await self.get(
            index,
            relpath=src,
        )
        if extra_data and extra_data.get("content-type"):
            fmt = extra_data.get("content-type").replace(" ", "")
        if data:
            if isinstance(data, str):
                data = data.encode("utf-8")
            return ("data:%s;base64," % fmt) + base64.b64encode(data).decode("utf-8")
        else:
            return absurl(index, src)

    css_encoding_re = re.compile(r"""@charset\s+["']([-_a-zA-Z0-9]+)["']\;""", re.I)

    async def handle_css_content(
        self,
        index,
        css,
    ):
        if not css:
            return css
        reg = re.compile(r"url\s*\((.+?)\)")

        urls = reg.findall(css)

        processed_urls = {}
        for url in urls:
            clean_url = url.strip(" '\"")
            processed_urls[url] = await self.data_to_base64(index, clean_url)

        # Do the replacement
        def repl(matchobj):
            src = matchobj.group(1).strip(" '\"")
            return "url(" + processed_urls[matchobj.group(1)] + ")"

        css = reg.sub(repl, css)
        return css

    async def generate(
        self,
        index,
        comment=True,
        keep_script=True,
        full_url=True,
        username=None,
        password=None,
    ) -> str:
        html_doc, extra_data = await self.get(
            index,
            username=username,
            password=password,
        )

        if extra_data and extra_data.get("url"):
            index = extra_data["url"]

        soup = BeautifulSoup(html_doc, "lxml")
        soup_title = soup.title.string if soup.title else ""

        for link in soup("link"):
            if link.get("href"):
                if (
                    "mask-icon" in (link.get("rel") or [])
                    or "icon" in (link.get("rel") or [])
                    or "apple-touch-icon" in (link.get("rel") or [])
                    or "apple-touch-icon-precomposed" in (link.get("rel") or [])
                ):
                    link["data-href"] = link["href"]
                    link["href"] = await self.data_to_base64(
                        index,
                        link["href"],
                    )
                elif (
                    link.get("type") == "text/css"
                    or link["href"].lower().endswith(".css")
                    or "stylesheet" in (link.get("rel") or [])
                ):
                    new_type = "text/css" if not link.get("type") else link["type"]
                    css = soup.new_tag("style", type=new_type)
                    css["data-href"] = link["href"]
                    for attr in link.attrs:
                        if attr in ["href"]:
                            continue
                        css[attr] = link[attr]
                    css_data, _ = await self.get(
                        index,
                        relpath=link["href"],
                    )
                    new_css_content = await self.handle_css_content(
                        absurl(index, link["href"]),
                        css_data,
                    )
                    css.string = new_css_content
                    link.replace_with(css)
                elif full_url:
                    link["data-href"] = link["href"]
                    link["href"] = absurl(index, link["href"])
        for js in soup("script"):
            if not keep_script:
                js.replace_with("")
                continue
            if not js.get("src"):
                continue
            new_type = (
                "text/javascript"
                if not js.has_attr("type") or not js["type"]
                else js["type"]
            )
            code = soup.new_tag("script", type=new_type)
            code["data-src"] = js["src"]
            js_str, _ = await self.get(index, relpath=js["src"])
            if isinstance(js_str, bytes):
                js_str = js_str.decode("utf-8")
            if js_str.find("</script>") > -1:
                code["src"] = (
                    "data:text/javascript;base64,"
                    + base64.b64encode(js_str.encode()).decode()
                )
            elif js_str.find("]]>") < 0:
                code.string = "<!--//--><![CDATA[//><!--\n" + js_str + "\n//--><!]]>"
            else:
                code.string = js_str
            js.replace_with(code)
        for img in soup("img"):
            if not img.get("src"):
                continue
            img["data-src"] = img["src"]
            img["src"] = await self.data_to_base64(
                index,
                img["src"],
            )

            if img.get("srcset"):
                img["data-srcset"] = img["srcset"]
                del img["srcset"]
                logging.warning(
                    "srcset found in img tag. Attribute will be cleared. File src => %s"
                    % (img["data-src"]),
                )

            def check_alt(attr):
                if img.has_attr(attr) and img[attr].startswith("this.src="):
                    logging.warning(
                        "%s found in img tag and unhandled, which may break page"
                        % (attr),
                    )

            check_alt("onerror")
            check_alt("onmouseover")
            check_alt("onmouseout")
        for tag in soup(True):
            if (
                full_url
                and tag.name == "a"
                and tag.has_attr("href")
                and not tag["href"].startswith("#")
            ):
                tag["data-href"] = tag["href"]
                tag["href"] = absurl(index, tag["href"])
            if tag.has_attr("style"):
                if tag["style"]:
                    tag["style"] = await self.handle_css_content(
                        index,
                        tag["style"],
                    )
            elif (
                tag.name == "link"
                and tag.has_attr("type")
                and tag["type"] == "text/css"
            ):
                if tag.string:
                    tag.string = await self.handle_css_content(
                        index,
                        tag.string,
                    )
            elif tag.name == "style":
                if tag.string:
                    tag.string = await self.handle_css_content(
                        index,
                        tag.string,
                    )

        # finally insert some info into comments
        if comment:
            for html in soup("html"):
                html.insert(
                    0,
                    BeautifulSoup(
                        "<!-- \n single html processed by https://github.com/zTrix/webpage2html\n "
                        "title: %s\n url: %s\n date: %s\n-->"
                        % (soup_title, index, datetime.datetime.now().ctime()),
                        "lxml",
                    ),
                )
                break
        return str(soup)


def main():
    agent = Agent(client=httpx.AsyncClient())
    import sys
    async def f():
        page = await agent.generate(sys.argv[1])
        return page
    print(asyncio.run(f()))
