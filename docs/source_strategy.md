# Journal source strategy

Last updated: 2026-07-27 (Asia/Shanghai)

## Production source order

### Nature

1. Official Nature RSS
2. DOI history deduplication
3. Metadata enrichment sources

### Science and Cell

1. Official publisher RSS is attempted first on every discovery run.
2. If the official RSS is blocked or fails to parse, Crossref is used as the DOI discovery fallback.
3. Europe PMC runs as a separate daily completeness audit and metadata-enrichment layer.

## Why official HTML pages are not in the GitHub-hosted production path

A dedicated diagnostic workflow tested seven official publication pages:

- Science First Release
- Science journal landing page
- Science current issue
- Cell Articles in Press
- Cell legacy in-press URL
- Cell journal landing page
- Cell current issue

Each page was tested with both a browser-like HTTP request and Playwright Chromium. All requests from the GitHub-hosted runner were intercepted by Cloudflare and returned HTTP 403 challenge pages. No article DOI, citation metadata, JSON-LD, Next.js payload, or reusable content API was exposed before the challenge.

Accordingly, official HTML scraping is not part of the current production source chain. This conclusion applies to GitHub-hosted runners; it may be revisited if the project later uses a stable self-hosted runner or a publisher-approved access route.

Historical diagnostics were performed during private development. One-off probe payloads and logs are retained only in the private archive and are not included in the public production repository.

## RSSHub conclusion

Public RSSHub instances were not reliable for the Science First Release and Cell Articles in Press routes. A temporary self-hosted RSSHub instance also failed: Science was blocked by the publisher with HTTP 403, and the tested Cell route was absent in the current RSSHub image.

The corresponding one-off RSSHub probe payloads and logs are retained only in the private archive and are not included in the public production repository.

RSSHub is therefore not part of the production discovery path.

## Current operational principle

Publisher RSS remains the preferred source and is always attempted. Crossref provides resilient DOI-level discovery when Science or Cell blocks automated RSS access. Europe PMC provides a delayed but independent completeness check and enriches records with PMID/PMCID, abstracts, authors, affiliations, publication types, and indexing dates when available.
