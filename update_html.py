import re

with open('coinscreener/screener/templates/screener/strategy_trading.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace UI texts
content = content.replace('봉 전', '봉 이내')
content = content.replace('봉전', '봉이내')

# Insert cross operators
def add_cross_ops(match):
    # Match contains the entire <select name="operator"...>...</select>
    block = match.group(0)
    
    # We only want to add these options if they are not already there
    if 'cross_up' not in block:
        # Find the position right before </select>
        pos = block.rfind('</select>')
        if pos != -1:
            new_options = '''
                        <option value="cross_up">상향 돌파 (Cross Up)</option>
                        <option value="cross_down">하향 돌파 (Cross Down)</option>
                    '''
            block = block[:pos] + new_options + block[pos:]
            
    return block

content = re.sub(r'<select name=\"operator\"[^>]*>.*?</select>', add_cross_ops, content, flags=re.DOTALL)

with open('coinscreener/screener/templates/screener/strategy_trading.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("HTML update complete.")
