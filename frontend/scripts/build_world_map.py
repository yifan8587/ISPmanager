#!/usr/bin/env python3
"""为 world-map.svg 着色并优化清晰度相关样式。"""

from __future__ import annotations

import re
from pathlib import Path

SVG_PATH = Path(__file__).resolve().parents[1] / 'public' / 'world-map.svg'

REGION_COLORS = {
    'north_america': '#bfdbfe',
    'south_america': '#93c5fd',
    'europe': '#bae6fd',
    'africa': '#a5b4fc',
    'middle_east': '#7dd3fc',
    'central_asia': '#60a5fa',
    'east_asia': '#38bdf8',
    'south_asia': '#22d3ee',
    'southeast_asia': '#06b6d4',
    'oceania': '#67e8f9',
    'russia': '#c7d2fe',
    'antarctica': '#f0f9ff',
    'default': '#dbeafe',
}

SKIP_IDS = {
    'ocean-bg', 'country-labels', 'region-labels', 'pop-markers', 'landmasses',
    'svg2', 'metadata346', 'defs344', 'map-defs', 'path1', 'path-1',
    'namedview', 'metadata', 'defs',
}

MAP_DEFS = '''<defs id="map-defs">
<style type="text/css"><![CDATA[
  .land-path {
    shape-rendering: geometricPrecision;
    stroke-linejoin: round;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }
  .country-name {
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    pointer-events: none;
    text-rendering: geometricPrecision;
  }
]]></style>
<linearGradient id="ocean-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
  <stop offset="0%" stop-color="#e0f2fe"/>
  <stop offset="55%" stop-color="#bae6fd"/>
  <stop offset="100%" stop-color="#7dd3fc"/>
</linearGradient>
<filter id="coast-shadow" x="-2%" y="-2%" width="104%" height="104%">
  <feDropShadow dx="0" dy="0" stdDeviation="0.4" flood-color="#0284c7" flood-opacity="0.18"/>
</filter>
</defs>'''


def classify(cid: str) -> str:
    c = cid.lower().replace('-', '_').replace(' ', '_')
    if c in {'usa', 'canada', 'mexico', 'greenland', 'alaska', 'cuba', 'jamaica', 'haiti',
             'panama', 'belize', 'bahamas', 'bermuda', 'baffin', 'guatemala', 'honduras',
             'nicaragua', 'dominican', 'puerto', 'martinique', 'guadeloupe'}:
        return 'north_america'
    if 'aleutian' in c or c.startswith(('adak', 'attu', 'amchitka')):
        return 'north_america'
    if c in {'brazil', 'argentina', 'chile', 'peru', 'colombia', 'venezuela', 'ecuador',
             'bolivia', 'paraguay', 'uruguay', 'guyana', 'suriname', 'falkland', 'patagonia'}:
        return 'south_america'
    africa = {
        'algeria', 'morocco', 'egypt', 'libya', 'tunisia', 'sudan', 'south_sudan', 'ethiopia',
        'somalia', 'kenya', 'tanzania', 'uganda', 'rwanda', 'burundi', 'drc', 'congo', 'gabon',
        'cameroon', 'nigeria', 'ghana', 'ivoire', 'liberia', 'guinee', 'senegal', 'gambia', 'mali',
        'niger', 'burkina', 'benin', 'togo', 'chad', 'centrafrique', 'angola', 'zambia', 'malawi',
        'mozambique', 'zimbabwe', 'namibia', 'botswana', 'swaziland', 'lesotho', 'mauretania',
        'casamance', 'bissau', 'eritrea', 'djibouti', 'somaliland', 'soqotra', 'principe',
        'bioko', 'cabinda', 'madagascar', 'mauritius', 'reunion', 'comoros', 'seychelles',
    }
    if c in africa:
        return 'africa'
    if c in {'saudi', 'yemen', 'oman', 'uae', 'qatar', 'bahrain', 'kuwait', 'iraq', 'iran',
             'israel', 'jordan', 'lebanon', 'syria', 'turkey', 'cyprus', 'georgia', 'armenia',
             'azerbaijan', 'afghanistan', 'pakistan'}:
        return 'middle_east'
    if c in {'china', 'japan', 'korea', 'taiwan', 'mongolia', 'tibet', 'north_korea', 'south_korea',
             'hong_kong', 'macau', 'manchuria'}:
        return 'east_asia'
    if c in {'india', 'bangladesh', 'sri_lanka', 'nepal', 'bhutan', 'maldives', 'pakistan'}:
        return 'south_asia'
    if c in {'indonesia', 'malaysia', 'thailand', 'vietnam', 'philippines', 'myanmar',
             'cambodia', 'laos', 'singapore', 'brunei', 'timor', 'java', 'sumatra', 'borneo',
             'kalimantan', 'sulawesi', 'maluku', 'seram', 'bali', 'lombok', 'sumba', 'flores',
             'papua', 'bougainville', 'choiseul', 'malaita', 'rennell', 'mindanao', 'luzon'}:
        return 'southeast_asia'
    if any(x in c for x in ('java', 'sumatra', 'borneo', 'sulawesi', 'maluku', 'bali', 'lombok',
                            'timor', 'papua', 'solomon', 'vanuatu', 'fiji', 'samoa', 'polynesia',
                            'melanesia', 'micronesia', 'kalimantan', 'mindanao', 'luzon')):
        return 'southeast_asia'
    if c in {'australia', 'new_zealand', 'tasmania', 'zealand', 'fiji', 'samoa', 'tonga',
             'vanuatu', 'guam', 'palau', 'hawaii', 'polynesia', 'melanesia', 'micronesia'}:
        return 'oceania'
    if 'australia' in c or 'zealand' in c or 'tasmania' in c:
        return 'oceania'
    if c == 'russia' or c in {'kazakhstan', 'uzbekistan', 'turkmenistan', 'kyrgyzstan', 'tajikistan'}:
        return 'russia' if c == 'russia' else 'central_asia'
    if c in {'france', 'germany', 'spain', 'portugal', 'italy', 'uk', 'england', 'scotland',
             'wales', 'ireland', 'iceland', 'norway', 'sweden', 'finland', 'denmark', 'poland',
             'ukraine', 'belarus', 'romania', 'bulgaria', 'greece', 'hungary', 'austria',
             'switzerland', 'czech', 'slovakia', 'slovenia', 'croatia', 'bosnia', 'serbia',
             'montenegro', 'albania', 'macedonia', 'moldova', 'estonia', 'latvia', 'lithuania',
             'netherlands', 'belgium', 'luxembourg', 'andorra', 'monaco', 'malta', 'cyprus',
             'sardinia', 'sicily', 'corsica', 'crete', 'thrace', 'kaliningrad', 'svalbard',
             'faroe', 'gotland', 'bornholm'}:
        return 'europe'
    if 'antarctic' in c:
        return 'antarctica'
    return 'default'


def style_path(tag: str, cid: str) -> str:
    region = classify(cid)
    color = REGION_COLORS.get(region, REGION_COLORS['default'])
    style = f'fill:{color};stroke:#0369a1;stroke-width:0.55;fill-rule:evenodd'
    tag = re.sub(r'(\sclass="land-path")+', '', tag)
    tag = re.sub(r'<path\b', '<path class="land-path"', tag, count=1)
    if 'style=' in tag:
        return re.sub(r'style="[^"]*"', f'style="{style}"', tag)
    return tag.replace('<path class="land-path"', f'<path class="land-path" style="{style}"', 1)


def process(content: str) -> str:
    content = re.sub(r'class="land-path"\s+class="land-path"', 'class="land-path"', content)
    content = re.sub(r'<g id="region-labels"[\s\S]*?</g>', '', content)
    content = re.sub(r'<rect id="ocean-bg"[^/]*/>', '', content)
    content = re.sub(r'<g id="country-labels"[\s\S]*?</g>', '', content)
    content = re.sub(r'<defs id="map-defs"[\s\S]*?</defs>', '', content)

    def repl(m):
        cid = m.group(2)
        if cid in SKIP_IDS or cid.startswith('path-effect'):
            return m.group(0)
        return style_path(m.group(0), cid)

    content = re.sub(r'(<path[^>]*?\bid="([^"]+)"[^>]*>)', repl, content)

    if 'id="map-defs"' not in content:
        content = re.sub(r'(<svg[^>]*>)', rf'\1{MAP_DEFS}', content, count=1)

    if 'id="ocean-bg"' not in content:
        content = re.sub(
            r'(<filter id="coast-shadow"[\s\S]*?</filter>\s*</defs>)',
            r'\1<rect id="ocean-bg" x="0" y="0" width="950" height="620" fill="url(#ocean-gradient)"/>',
            content,
            count=1,
        )

    return content


def main():
    content = SVG_PATH.read_text(encoding='utf-8')
    SVG_PATH.write_text(process(content), encoding='utf-8')
    print(f'Updated {SVG_PATH}')


if __name__ == '__main__':
    main()
