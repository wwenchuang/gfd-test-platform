"""Inert offline HTML for bearer-token blob previews and downloaded files."""
import html
from html.parser import HTMLParser

CSP = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'"
META = '<meta http-equiv="Content-Security-Policy" content="'+CSP+'">'


def protect_export_html(document):
    # The canonical renderer already escapes every input and has no active links.
    return document.replace('<head>', '<head>'+META, 1)


class _InertPreview(HTMLParser):
    allowed = {'div','span','p','h1','h2','h3','h4','h5','h6','table','thead','tbody','tr','th','td',
               'ul','ol','li','pre','code','b','strong','em','i','br','hr','img','section','article'}
    suppressed = {'script','style','iframe','object','svg','math','template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self,tag,attrs):
        if self.hidden:
            if tag in self.suppressed: self.hidden.append(tag)
            return
        if tag in self.suppressed:
            self.hidden.append(tag)
            return
        if tag not in self.allowed: return
        safe = []
        for key,value in attrs:
            if key in {'colspan','rowspan','width','height'} and (value or '').isdigit():
                safe.append(f'{key}="{value}"')
            if tag == 'img' and key == 'src' and (value or '').startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,','data:image/gif;base64,')):
                safe.append('src="'+html.escape(value,quote=True)+'"')
        self.parts.append('<'+tag+(' '+' '.join(safe) if safe else '')+'>')

    def handle_endtag(self,tag):
        if self.hidden:
            if tag == self.hidden[-1]: self.hidden.pop()
            return
        if tag in self.allowed: self.parts.append('</'+tag+'>')

    def handle_data(self,data):
        if not self.hidden: self.parts.append(html.escape(data))


def inert_evidence_html(content):
    parser = _InertPreview()
    parser.feed(content.decode('utf-8',errors='replace'))
    parser.close()
    return ('<!doctype html><html><head><meta charset="utf-8">'+META+'</head><body>'
            '<p>原始证据的交互脚本与外部链接已停用。此处仅显示可安全提取的静态内容；'
            'JavaScript 生成的内容可能无法显示，请以本批测试报告和截图为准。原始字节已私有保留。</p>'
            +''.join(parser.parts)+'</body></html>').encode()
