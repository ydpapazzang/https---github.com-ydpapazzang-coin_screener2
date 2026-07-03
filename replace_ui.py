import re

with open('coinscreener/screener/templates/screener/strategy_trading.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove hidden offset inputs
content = re.sub(r'\s*<input type=\"hidden\" name=\"offset\" id=\"[a-z_]+_offset\" value=\"0\">', '', content)

# 2. Replace the select + slider with simple select
prefixes = ['ma', 'rsi', 'bb', 'ha', 'ic', 'volume']
for prefix in prefixes:
    pattern = r'<select onchange=\"syncOffsetType\(\'(?i)' + prefix + r'\'\)\" id=\"' + prefix + r'_offset_type\">.*?<\/div>\s*<\/div>'
    upper_prefix = prefix.upper()
    pattern = r'<select onchange="syncOffsetType\(\'' + upper_prefix + r'\'\)" id="' + prefix + r'_offset_type">.*?</div?>\s*</div>'
    
    new_html = f'''<select name="offset" id="{prefix}_offset" style="width:140px;">
                        <option value="0">0봉 전 (현재봉)</option>
                        <option value="1">1봉 전</option>
                        <option value="2">2봉 전</option>
                        <option value="3">3봉 전</option>
                        <option value="4">4봉 전</option>
                        <option value="5">5봉 전</option>
                        <option value="6">6봉 전</option>
                        <option value="7">7봉 전</option>
                        <option value="8">8봉 전</option>
                        <option value="9">9봉 전</option>
                        <option value="10">10봉 전</option>
                    </select>
                </div>'''
    
    content = re.sub(pattern, new_html, content, flags=re.DOTALL)

with open('coinscreener/screener/templates/screener/strategy_trading.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Replaced HTML.')
