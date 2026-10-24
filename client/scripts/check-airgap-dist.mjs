// airgap(폐쇄망) 웹 빌드 산출물 검사 (01-plan 4.1, 4.5). `npm run build:airgap` 끝에 돈다. 하나라도 걸리면 exit 1.
// 1) Capacitor 흔적: Capacitor 패키지가 로컬 대역(src/airgap-shims)으로 바뀌어 번들에 없는지
// 2) 외부 절대 URL: 런타임에 외부로 요청을 보낼 만한 주소가 없는지. 요청하지 않는 문자열만 아래 허용 목록에 둔다
// 3) 위탁사 문구: 개인정보처리방침의 Clova·Azure 위탁 항목이 내부 처리 문구로 바뀌었는지
// 사용: node scripts/check-airgap-dist.mjs [검사할 디렉터리, 기본 dist-airgap]
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative } from 'node:path';

const TEXT_EXTENSIONS = new Set(['.js', '.mjs', '.css', '.html', '.json', '.webmanifest', '.svg', '.txt', '.map']);

const FORBIDDEN = [
  { name: 'Capacitor', pattern: /capacitor/gi },
  { name: '위탁사 문구', pattern: /clova|azure|microsoft|네이버클라우드/gi },
];

const ALLOWED_URLS = [
  { pattern: /^http:\/\/www\.w3\.org\/[\w/.]+$/, why: 'XML 네임스페이스 식별자(React DOM). 요청하지 않는다' },
  { pattern: /^https:\/\/reactjs\.org\/docs\/error-decoder\.html\?invariant=$/, why: 'React 운영 빌드의 오류 메시지 문구' },
  { pattern: /^http:\/\/localhost$/, why: 'axios가 브라우저 밖에서 URL을 해석할 때의 기준값. 브라우저에서는 window.location을 쓴다' },
];
const URL_PATTERN = /https?:\/\/[^\s"'`)<>\\]*/g;

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

function snippet(text, index) {
  return text.slice(Math.max(0, index - 40), index + 60).replace(/\s+/g, ' ');
}

const root = process.argv[2] ?? 'dist-airgap';
let files;
try {
  files = walk(root);
} catch {
  console.error(`airgap dist check: ${root} not found. Run vite build --mode airgap first.`);
  process.exit(1);
}

const problems = [];
let scanned = 0;
for (const file of files) {
  if (!TEXT_EXTENSIONS.has(extname(file))) {
    problems.push(`${relative(root, file)}: unexpected non-text file (add it to TEXT_EXTENSIONS after checking it)`);
    continue;
  }
  scanned += 1;
  const text = readFileSync(file, 'utf8');
  for (const { name, pattern } of FORBIDDEN) {
    for (const match of text.matchAll(pattern)) {
      problems.push(`${relative(root, file)}: ${name} "${match[0]}" … ${snippet(text, match.index)}`);
    }
  }
  for (const match of text.matchAll(URL_PATTERN)) {
    if (!ALLOWED_URLS.some(({ pattern }) => pattern.test(match[0]))) {
      problems.push(`${relative(root, file)}: external URL "${match[0]}" … ${snippet(text, match.index)}`);
    }
  }
}

if (problems.length) {
  console.error(`airgap dist check failed (${problems.length}):`);
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
console.log(`airgap dist check passed: ${scanned} file(s) in ${root}`);
