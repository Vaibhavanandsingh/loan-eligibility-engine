// Turn each fetched HTML page into compact, loan-relevant plain text.
//
// Why: a bank page can be 1 MB of HTML but only a few KB of loan terms, and the
// LLM charges by the amount of text it reads. Keeping only lines that talk about
// rates, income, age, credit score and so on also makes the hash steadier:
// rotating banners and footers no longer count as a "changed page".

const MAX_CHARS = 40000;
const MIN_USEFUL_CHARS = 500;

const RELEVANT = /(eligib|income|salar|\bage\b|years|cibil|credit score|interest|rate|%|₹|\brs\.?|lakh|crore|loan amount|tenure|employ|self[- ]employed|business|turnover|vintage|itr|minimum|maximum)/i;

const ENTITIES = {
  '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'",
  '&rsquo;': "'", '&lsquo;': "'", '&rdquo;': '"', '&ldquo;': '"', '&ndash;': '-', '&mdash;': '-',
  '&#8377;': '₹', '&#x20b9;': '₹', '&rupee;': '₹',
};

function htmlToLines(html) {
  const text = html
    .replace(/<(script|style|noscript|svg|head|iframe)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<(br|\/p|\/li|\/tr|\/h[1-6]|\/div|\/section|\/table|\/ul|\/ol)\b[^>]*>/gi, '\n')
    .replace(/<(td|th)\b[^>]*>/gi, ' | ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[#a-z0-9]+;/gi, (e) => ENTITIES[e.toLowerCase()]
      ?? (/^&#\d+;$/.test(e) ? String.fromCharCode(Number(e.slice(2, -1))) : ' '));
  return text.split('\n').map((l) => l.replace(/\s+/g, ' ').replace(/^[\s|]+|[\s|]+$/g, '').trim()).filter(Boolean);
}

// Keep relevant lines plus the line before each (usually the lender or section heading).
function relevantText(lines) {
  const keep = new Set();
  lines.forEach((line, i) => {
    if (RELEVANT.test(line) && line.length < 1000) {
      if (i > 0 && lines[i - 1].length < 120) keep.add(i - 1);
      keep.add(i);
    }
  });
  const out = [];
  for (const i of [...keep].sort((a, b) => a - b)) {
    if (out[out.length - 1] !== lines[i]) out.push(lines[i]); // drop repeated lines
  }
  return out.join('\n').slice(0, MAX_CHARS);
}

const sources = $('Load sources').all().map((i) => i.json);

return $input.all().map((item, index) => {
  const source = sources[item.pairedItem?.item ?? index];
  const html = typeof item.json.html === 'string' ? item.json.html : '';
  const fetchError = item.json.error ? String(item.json.error.message ?? item.json.error).slice(0, 300) : null;
  const text = html ? relevantText(htmlToLines(html)) : '';
  return {
    json: {
      source_site: source.source_site,
      url: source.url,
      previous_hash: source.previous_hash,
      fetch_ok: !fetchError && html.length > 0,
      fetch_error: fetchError,
      html_kb: Math.round(html.length / 1024),
      chars: text.length,
      useful: text.length >= MIN_USEFUL_CHARS,
      text,
    },
  };
});
