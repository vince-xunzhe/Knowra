import React, { useMemo, useState } from 'react'
import { ActivityIndicator, StyleSheet, View } from 'react-native'
import { WebView } from 'react-native-webview'

/**
 * Renders Markdown + LaTeX math faithfully, matching the desktop
 * 回顾 / wiki experience (same KaTeX engine).
 *
 * Why a WebView? The wiki .md bodies contain block (`$$…$$`) and inline
 * (`$…$`) LaTeX. There's no solid pure-RN KaTeX renderer; the robust
 * path everyone uses is a WebView running KaTeX's auto-render. We load
 * marked (markdown→HTML) + KaTeX from a CDN. That's acceptable here
 * because the content itself was just fetched over the network (the
 * signed Storage URL) — if the device can pull the .md, it can pull
 * the CDN libs. If the CDN is somehow blocked, the text + markdown
 * still render; only the math falls back to raw source (no worse than
 * before).
 *
 * The WebView auto-sizes to content height via a postMessage handshake
 * so it sits inline in a ScrollView without its own scrollbar.
 */

const KATEX_VER = '0.16.11'
const MARKED_VER = '12.0.2'

function buildHtml(markdown: string): string {
  // JSON.stringify safely escapes the markdown into a JS string literal
  // (handles quotes, backslashes, newlines — critical for LaTeX which
  // is full of backslashes).
  const payload = JSON.stringify(markdown)
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@${KATEX_VER}/dist/katex.min.css" />
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; padding: 0; background: #0b0d12; }
  body {
    color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 15px; line-height: 1.7;
    padding: 16px; padding-bottom: 32px;
    -webkit-text-size-adjust: 100%;
    word-wrap: break-word; overflow-wrap: break-word;
  }
  h1 { font-size: 22px; font-weight: 700; margin: 0 0 14px; color: #f1f5f9; line-height: 1.3; }
  h2 { font-size: 18px; font-weight: 700; margin: 22px 0 10px; color: #e2e8f0;
       border-bottom: 1px solid #1e293b; padding-bottom: 6px; }
  h3 { font-size: 16px; font-weight: 600; margin: 18px 0 8px; color: #cbd5e1; }
  p { margin: 0 0 12px; }
  a { color: #818cf8; text-decoration: none; }
  strong { color: #f1f5f9; }
  em { color: #cbd5e1; }
  ul, ol { padding-left: 22px; margin: 0 0 12px; }
  li { margin: 4px 0; }
  blockquote { border-left: 3px solid #4f46e5; margin: 12px 0; padding: 4px 14px;
               color: #94a3b8; background: rgba(79,70,229,0.08); border-radius: 4px; }
  code { background: #1e293b; padding: 2px 6px; border-radius: 4px;
         font-family: Menlo, monospace; font-size: 13px; color: #fcd34d; }
  pre { background: #020617; border: 1px solid #1e293b; border-radius: 8px;
        padding: 12px; overflow-x: auto; }
  pre code { background: none; padding: 0; color: #cbd5e1; }
  hr { border: none; border-top: 1px solid #1e293b; margin: 18px 0; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; display: block; overflow-x: auto; }
  th, td { border: 1px solid #1e293b; padding: 6px 10px; text-align: left; }
  th { background: #0f1117; color: #cbd5e1; }
  .katex-display { overflow-x: auto; overflow-y: hidden; padding: 4px 0; }
  .katex { font-size: 1.02em; }
  img { max-width: 100%; height: auto; border-radius: 6px; }
</style>
</head>
<body>
<div id="content"></div>
<script src="https://cdn.jsdelivr.net/npm/marked@${MARKED_VER}/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@${KATEX_VER}/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@${KATEX_VER}/dist/contrib/auto-render.min.js"></script>
<script>
  var md = ${payload};
  var el = document.getElementById('content');
  function reportHeight() {
    var h = document.body.scrollHeight;
    if (window.ReactNativeWebView) {
      window.ReactNativeWebView.postMessage(String(h));
    }
  }
  try {
    el.innerHTML = (window.marked ? window.marked.parse(md) : md.replace(/\\n/g, '<br/>'));
  } catch (e) {
    el.textContent = md;
  }
  function doRender() {
    try {
      if (window.renderMathInElement) {
        window.renderMathInElement(el, {
          delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '\\\\[', right: '\\\\]', display: true },
            { left: '$', right: '$', display: false },
            { left: '\\\\(', right: '\\\\)', display: false }
          ],
          throwOnError: false,
          ignoredTags: ['script','noscript','style','textarea','pre','code']
        });
      }
    } catch (e) {}
    reportHeight();
  }
  // Math libs may still be loading; try now + after load + a couple of
  // retries so a slow CDN doesn't leave math unrendered.
  doRender();
  window.addEventListener('load', doRender);
  setTimeout(doRender, 600);
  setTimeout(doRender, 1800);
</script>
</body>
</html>`
}

export default function MarkdownMathView({ markdown }: { markdown: string }) {
  const html = useMemo(() => buildHtml(markdown), [markdown])
  const [height, setHeight] = useState(400)

  return (
    <View style={[styles.wrap, { height }]}>
      <WebView
        originWhitelist={['*']}
        source={{ html }}
        style={styles.web}
        scrollEnabled={false}
        showsVerticalScrollIndicator={false}
        // Let the outer ScrollView own scrolling; the WebView reports
        // its content height so it expands to fit.
        onMessage={e => {
          const h = Number(e.nativeEvent.data)
          if (!Number.isNaN(h) && h > 0) setHeight(Math.ceil(h))
        }}
        // Dark background while loading so there's no white flash.
        renderLoading={() => (
          <View style={styles.loading}>
            <ActivityIndicator color="#818cf8" />
          </View>
        )}
        startInLoadingState
      />
    </View>
  )
}

const styles = StyleSheet.create({
  wrap: { backgroundColor: '#0b0d12', width: '100%' },
  web: { backgroundColor: '#0b0d12', flex: 1 },
  loading: {
    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
    alignItems: 'center', justifyContent: 'center', backgroundColor: '#0b0d12',
  },
})
