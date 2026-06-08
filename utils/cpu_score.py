import cloudscraper
from bs4 import BeautifulSoup
from typing import Tuple
import cpuinfo
import re


class GeekbenchScraper:
    """Geekbench 爬虫类，自动识别本地 CPU"""

    def __init__(self):
        self.scraper = cloudscraper.create_scraper()
        self.base_url = "https://browser.geekbench.com/search"
        self.local_cpu = self._get_local_cpu()

    def _get_local_cpu(self) -> dict:
        """获取本地 CPU 信息"""
        try:
            cpu_info = cpuinfo.get_cpu_info()
            brand_raw = cpu_info.get('brand_raw', '')

            # 提取搜索关键词
            patterns = [
                r'Ryzen\s+\d+\s+(\d{4}[A-Z0-9]*)',
                r'(\d{4,5}[A-Z]*(?:X3D|XT)?)',
                r'[iI]\d+[-]?(\d{4,5}[KFS]*(?:T)?)',
                r'(M\d+(?:\s*(?:Pro|Max|Ultra))?)',
                r'(\d{4,5}[A-Z]*)',
            ]

            keyword = ''
            for pattern in patterns:
                match = re.search(pattern, brand_raw, re.IGNORECASE)
                if match:
                    keyword = match.group(1).replace(' ', '')
                    if len(keyword) >= 3:
                        break

            return {
                'full_name': brand_raw,
                'cores': cpu_info.get('count', 0),
                'search_keyword': keyword
            }
        except Exception:
            return {'full_name': 'Unknown', 'cores': 0, 'search_keyword': ''}

    def get_local_cpu_scores(self) -> Tuple[int, int]:
        """获取本地 CPU 的平均跑分"""
        keyword = self.local_cpu['search_keyword']
        if not keyword:
            return (0, 0)
        return self.get_average_scores(keyword)

    def get_average_scores(self, cpu_query: str) -> Tuple[int, int]:
        """获取指定 CPU 的平均跑分"""
        try:
            response = self.scraper.get(
                self.base_url,
                params={'q': cpu_query},
                timeout=15
            )

            if response.status_code != 200:
                return (0, 0)

            scores = self._parse_scores(response.text)

            if not scores:
                return (0, 0)

            avg_single = sum(s['single'] for s in scores) // len(scores)
            avg_multi = sum(s['multi'] for s in scores) // len(scores)

            return (avg_single, avg_multi)

        except Exception:
            return (0, 0)

    def _parse_scores(self, html: str) -> list:
        """解析 HTML 提取分数"""
        soup = BeautifulSoup(html, 'html.parser')
        scores = []

        for item in soup.find_all('div', class_='list-col'):
            inner = item.find('div', class_='list-col-inner')
            if not inner:
                continue

            score_spans = inner.find_all('span', class_='list-col-text-score')
            if len(score_spans) >= 2:
                single = score_spans[0].text.strip()
                multi = score_spans[1].text.strip()

                if single.isdigit() and multi.isdigit():
                    scores.append({
                        'single': int(single),
                        'multi': int(multi)
                    })

        return scores


# if __name__ == "__main__":
#     scraper = GeekbenchScraper()
#     print(scraper.get_average_scores("1230 V3"))
    # print(f"本地 CPU: {scraper.local_cpu['full_name']}")
    # print(f"搜索关键词: {scraper.local_cpu['search_keyword']}")

    # single, multi = scraper.get_local_cpu_scores()
