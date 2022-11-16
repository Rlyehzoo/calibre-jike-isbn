#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
# License: GPLv3 Copyright: 2011, Kovid Goyal <kovid at kovidgoyal.net>
from __future__ import absolute_import, division, print_function, unicode_literals

import hashlib
import re
import time
try:
    from queue import Empty, Queue
except ImportError:
    from Queue import Empty, Queue

from calibre import as_unicode
from calibre.ebooks.chardet import xml_to_unicode
from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Option, Source
from calibre.utils.cleantext import clean_ascii_chars
from calibre.utils.localization import canonicalize_lang

NAMESPACES = {
    'openSearch': 'http://a9.com/-/spec/opensearchrss/1.0/',
    'atom': 'http://www.w3.org/2005/Atom',
    'dc': 'http://purl.org/dc/terms',
    'gd': 'http://schemas.google.com/g/2005'
}


def get_details(browser, url, timeout):  # {{{
    try:
        raw = browser.open_novisit(url, timeout=timeout).read()
    except Exception as e:
        print('try here 1 ?')
        gc = getattr(e, 'getcode', lambda: -1)
        if gc() != 403:
            raise
        # Google is throttling us, wait a little
        time.sleep(2)
        raw = browser.open_novisit(url, timeout=timeout).read()

    return raw


# }}}

xpath_cache = {}


def XPath(x):
    ans = xpath_cache.get(x)
    if ans is None:
        from lxml import etree
        ans = xpath_cache[x] = etree.XPath(x, namespaces=NAMESPACES)
    return ans


def cleanup_title(title):
    if ':' in title:
        return title.partition(':')[0]
    return re.sub(r'(.+?) \(.+\)', r'\1', title)

def get_isbn_url(isbns):
    try:
        from urllib.parse import urlencode
    except ImportError:
        from urllib import urlencode
    ISBN_URL = "https://api.jike.xyz/situ/book/isbn/"
    
    urls = []

    for isbn in isbns:
        print('isbn is ',isbn)
        isbn=isbn.strip()
        url = ISBN_URL + isbn
        urls.append(url)
        
    return urls


def to_metadata(browser, log, entry_, timeout):  # {{{
    from calibre.utils.date import parse_date, utcnow
    import re

    douban_id = str(entry_.get("douban"))
    log('douban_id',douban_id)
    title = entry_.get("name")
    log('title',title)
    description = entry_.get("description")
    # subtitle = entry_.get('subtitle')  # TODO: std metada doesn't have this field
    isbn = str(entry_.get("id"))  # ISBN11 is obsolute, use ISBN13
    cover_url = entry_.get("photoUrl")
    publisher = entry_.get("publishing")
    pubdate = entry_.get("published")
    authors = entry_.get("author")
    rating = entry_.get("doubanScore")

    if not authors:
        authors = [_('Unknown')]
    else:
        authors=[authors]
    if not douban_id or not title:
        # Silently discard this entry
        return None

    mi = Metadata(title, authors)
    mi.identifiers = {"douban": douban_id}
    mi.publisher = publisher
    mi.comments = description

    # ISBN
    isbns = []
    if isinstance(isbn, (type(""), bytes)):
        if check_isbn(isbn):
            isbns.append(isbn)
    else:
        for x in isbn:
            if check_isbn(x):
                isbns.append(x)
    if isbns:
        mi.isbn = sorted(isbns, key=len)[-1]
    mi.all_isbns = isbns

    # Ratings
    if rating:
        try:
            mi.rating = rating / 200.0
        except:
            log.exception("Failed to parse rating")
            mi.rating = 0

    # Cover
    mi.has_douban_cover = None
    u = cover_url
    if u:
        # If URL contains "book-default", the book doesn't have a cover
        if u.find("book-default") == -1:
            mi.has_douban_cover = u

    # pubdate
    if pubdate:
        try:
            default = utcnow().replace(day=15)
            mi.pubdate = parse_date(pubdate, assume_utc=True, default=default)
        except:
            log.error("Failed to parse pubdate %r" % pubdate)

    # Tags
    # mi.tags = tags

    ## pubdate
    #pubdate = get_text(extra, date)
    #if pubdate:
    #    from calibre.utils.date import parse_date, utcnow
    #    try:
    #        default = utcnow().replace(day=15)
    #        mi.pubdate = parse_date(pubdate, assume_utc=True, default=default)
    #    except:
    #        log.error('Failed to parse pubdate %r' % pubdate)

    return mi



def get_isbns(browser, log, entry_,timeout):  # {{{
    from lxml import etree
    
    entry = XPath('//atom:entry')
    entry_id = XPath('descendant::atom:id')
    url = XPath('descendant::atom:link[@rel="self"]/@href')
    creator = XPath('descendant::dc:creator')
    identifier = XPath('descendant::dc:identifier')
    title = XPath('descendant::dc:title')
    date = XPath('descendant::dc:date')
    publisher = XPath('descendant::dc:publisher')
    subject = XPath('descendant::dc:subject')
    description = XPath('descendant::dc:description')
    language = XPath('descendant::dc:language')

    id_url = entry_id(entry_)[0].text
    google_id = id_url.split('/')[-1]
    details_url = url(entry_)[0]
    title_ = ': '.join([x.text for x in title(entry_)]).strip()
    authors = [x.text.strip() for x in creator(entry_) if x.text]

    # ISBN
    isbns = []
    
    try:
        raw = get_details(browser, details_url, timeout)
        feed = etree.fromstring(
            xml_to_unicode(clean_ascii_chars(raw), strip_encoding_pats=True)[0],
            parser=etree.XMLParser(recover=True, no_network=True, resolve_entities=False)
        )
        extra = entry(feed)[0]
    except:
        log.exception('Failed to get additional details for')
        return None

    for x in identifier(extra):
        t = type('')(x.text).strip()
        if t[:5].upper() in ('ISBN:', 'LCCN:', 'OCLC:'):
            if t[:5].upper() == 'ISBN:':
                t = check_isbn(t[5:])
                if len(t) == 13:
                    isbns.append(t)
    return isbns

# }}}


class jike(Source):

    name = "jike isbn"
    author = "Rlyehzoo"
    version = (1, 1, 0)
    minimum_calibre_version = (1, 0, 0)

    description = _(
        "Downloads metadata and covers based on https://jike.xyz/api/isbn.html"
        "Useful only for Chinese language books."
    )

    capabilities = frozenset({'identify', 'cover'})
    touched_fields = frozenset({
        'title', 'authors', 'tags', 'pubdate', 'comments', 'publisher',
        'identifier:isbn', 'identifier:douban'
    })
    supports_gzip_transfer_encoding = True
    cached_cover_url_is_reliable = False

    DOUBAN_API_URL = "https://api.douban.com/v2/book/search"
    DOUBAN_BOOK_URL = "https://book.douban.com/subject/%s/"
    GOOGLE_COVER = 'https://books.google.com/books?id=%s&printsec=frontcover&img=1'

    options = (
        Option(
            "include_subtitle_in_title",
            "bool",
            True,
            _("Include subtitle in book title:"),
            _("Whether to append subtitle in the book title."),
        ),
        Option(
            "apikey", "string", "", _("jike api apikey"), _("jike api apikey")
        ),
    )


    def get_book_url(self, identifiers):  # {{{
        db = identifiers.get("douban", None)
        if db is not None:
            return ("douban", db, self.DOUBAN_BOOK_URL % db)

    # }}}

    def create_query(self, log, title=None, authors=None, identifiers={}):  # {{{
        try:
            from urllib.parse import urlencode
        except ImportError:
            from urllib import urlencode
        BASE_URL = 'https://books.google.com/books/feeds/volumes?'
        ISBN_URL = "https://api.jike.xyz/situ/book/isbn/"

        isbn = check_isbn(identifiers.get('isbn', None))
        q = ''
        t = None
        if isbn is not None:
            q = isbn
            t = "isbn"
        elif title or authors:
            log.error('now we can only search based on isbn')
            return
            authors = None

            def build_term(prefix, parts):
                return ' '.join('in' + prefix + ':' + x for x in parts)

            title_tokens = list(self.get_title_tokens(title))
            if title_tokens:
                q += build_term('title', title_tokens)
            author_tokens = list(self.get_author_tokens(authors, only_first_author=True))
            if author_tokens:
                q += ('+' if q else '') + build_term('author', author_tokens)

        if not q:
            return None

        url = None

        if t == "isbn":
            url = ISBN_URL + q
            return url
        else:
            if not isinstance(q, bytes):
                q = q.encode('utf-8')
            return BASE_URL + urlencode({
                'q': q,
                'max-results': 20,
                'start-index': 1,
                'min-viewability': 'none',
            })

    # }}}

    def download_cover(  # {{{
        self,
        log,
        result_queue,
        abort,
        title=None,
        authors=None,
        identifiers={},
        timeout=30,
        get_best_cover=False
    ):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(
                log,
                rq,
                abort,
                title=title,
                authors=authors,
                identifiers=identifiers
            )
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(
                key=self.identify_results_keygen(
                    title=title, authors=authors, identifiers=identifiers
                )
            )
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break
        if cached_url is None:
            log.info('No cover found')
            return

        br = self.browser

        log("Downloading cover from:", cached_url)

        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except:
            log.exception("Failed to download cover from:", cached_url)

#        for candidate in (0, 1):
#            if abort.is_set():
#                return
#            url = cached_url + '&zoom={}'.format(candidate)
#            log('Downloading cover from:', cached_url)
#            try:
#                cdata = br.open_novisit(url, timeout=timeout).read()
#                if cdata:
#                    if hashlib.md5(cdata).hexdigest() in self.DUMMY_IMAGE_MD5:
#                        log.warning('Google returned a dummy image, ignoring')
#                    else:
#                        result_queue.put((self, cdata))
#                        break
#            except Exception:
#                log.exception('Failed to download cover from:', cached_url)

    # }}}

    def get_cached_cover_url(self, identifiers):  # {{{
        url = None
        db = identifiers.get("douban", None)
        if db is None:
            isbn = identifiers.get("isbn", None)
            if isbn is not None:
                db = self.cached_isbn_to_identifier(isbn)
        if db is not None:
            url = self.cached_identifier_to_cover_url(db)

        return url

    # }}}

    def get_all_details(  # {{{
        self,
        br,
        log,
        entries,
        abort,
        result_queue,
        timeout
    ):
        from lxml import etree
        for relevance, i in enumerate(entries):
            try:
                ans = to_metadata(br, log, i, timeout)
                if isinstance(ans, Metadata):
                    ans.source_relevance = relevance
                    db = ans.identifiers["douban"]
                    for isbn in getattr(ans, 'all_isbns', []):
                        self.cache_isbn_to_identifier(isbn, db)
                    if ans.has_douban_cover:
                        self.cache_identifier_to_cover_url(db, ans.has_douban_cover)
                    self.clean_downloaded_metadata(ans)
                    result_queue.put(ans)
            except:
                log.exception(
                    'Failed to get metadata for identify entry:', etree.tostring(i)
                )
            if abort.is_set():
                break

    # }}}

    def identify(  # {{{
        self,
        log,
        result_queue,
        abort,
        title=None,
        authors=None,
        identifiers={},
        timeout=30
    ):
        from lxml import etree
        import json
        entry = XPath('//atom:entry')

        # check apikey
        if not self.prefs.get("apikey"):
            log.error('We need a apikey')
            return
        apikey=self.prefs.get("apikey")
        query = self.create_query(
            log, title=title, authors=authors, identifiers=identifiers
        )
        if not query:
            log.error('Insufficient metadata to construct query')
            return
        query=query+'?apikey='+apikey
        isbn = check_isbn(identifiers.get("isbn", None))
        log('Making query:', query)
        br = self.browser
        if isbn is not None:
            #br.addheaders = [
            #    ('apikey', self.prefs["apikey"]),
            #]

            try:
                raw = br.open_novisit(query, timeout=timeout).read()
            except Exception as e:
                log.exception("Failed to make identify query: %r" % query)
                return as_unicode(e)
            try:
                j = json.loads(raw)
            except Exception as e:
                log.exception("Failed to parse identify results")
                return as_unicode(e)
            if "data" in j:
                j=j["data"]
                entries = [j]
            else:
                entries = []
                entries.append(j)
            if not entries and identifiers and title and authors and not abort.is_set():
                return self.identify(
                    log, result_queue, abort, title=title, authors=authors, timeout=timeout
                )
            log('here entries are',entries)
        else:
            log.error('now we can only search based on isbn')
            return
            try:
                raw = br.open_novisit(query, timeout=timeout).read()
            except Exception as e:
                log.exception('Failed to make identify query: %r' % query)
                return as_unicode(e)

            try:
                feed = etree.fromstring(
                    xml_to_unicode(clean_ascii_chars(raw), strip_encoding_pats=True)[0],
                    parser=etree.XMLParser(recover=True, no_network=True, resolve_entities=False)
                )
                entries = entry(feed)
            except Exception as e:
                log.exception('Failed to parse identify results')
                return as_unicode(e)

            if not entries and title and not abort.is_set():
                if identifiers:
                    log('No results found, retrying without identifiers')
                    return self.identify(
                        log,
                        result_queue,
                        abort,
                        title=title,
                        authors=authors,
                        timeout=timeout
                    )
                ntitle = cleanup_title(title)
                if ntitle and ntitle != title:
                    log('No results found, retrying without sub-title')
                    return self.identify(
                        log,
                        result_queue,
                        abort,
                        title=ntitle,
                        authors=authors,
                        timeout=timeout
                    )

            #self.get_all_details(br, log, entries, abort, result_queue, timeout)
            isbns = []
            for relevance, i in enumerate(entries):
                isbn_i = get_isbns(br, log, i, timeout)
                time.sleep(2)
                isbns.extend(isbn_i)

            queries = get_isbn_url(isbns)

            entries = None

            for query in queries:
                br = self.browser
                br.addheaders = [
                    ('apikey', self.prefs["apikey"]),
                ]
                log('apikey is ',self.prefs["apikey"])
                try:
                    raw = br.open_novisit(query, timeout=timeout).read()
                except Exception as e:
                    break
                try:
                    j = json.loads(raw)
                except Exception as e:
                    log.exception("Failed to parse identify results")
                    return as_unicode(e)
                if "data" in j:
                    j = j["data"]
                if j is not None:
                    if entries is None:
                        #log('here111')
                        entries = [j]
                    else:
                        #log('here222')
                        #log('type0',type(entries))
                        entry=[j]
                        new = entries+entry
                        entries = new
                        #log('type1',type(j))
                        #log('type2',type([j]))
                        #log('type2.5',type(entry))
                        #log('type2.2',type(new))
                        #log('type3',type(entries))
                #log('this entry is', entries)
                
                

                if not entries and identifiers and title and authors and not abort.is_set():
                    return self.identify(
                        log, result_queue, abort, title=title, authors=authors, timeout=timeout
                    )
                # There is no point running these queries in threads as douban
                # throttles requests returning 403 Forbidden errors
        self.get_all_details(br, log, entries, abort, result_queue, timeout)
    # }}}

if __name__ == '__main__':  # tests {{{
    # To run these test use: calibre-debug
    # src/calibre/ebooks/metadata/sources/google.py
    from calibre.ebooks.metadata.sources.test import (
        test_identify_plugin, title_test, authors_test
    )
    tests = [
        #(
        #       {
        #           "identifiers": {"isbn": " 9787542663764"},
        #           "title": "八月炮火",
        #           "authors": ["巴巴拉·塔奇曼"],
        #       },
        #       [title_test("八月炮火", exact=True), authors_test(["巴巴拉·塔奇曼"])],
        #   ),
            (
                {"title": "新名字的故事"},
                [title_test("新名字的故事", exact=False)]
            )
    ]
    test_identify_plugin(jike.name, tests[:])

# }}}


