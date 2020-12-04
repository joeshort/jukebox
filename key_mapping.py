
# mapping from key code to original characters on numpad
CODE_TO_ORIG = {
             56: '=', 98: '/', 55: '*',
    71: '7', 72: '8', 73: '9', 74: '-',
    75: '4', 76: '5', 77: '6', 78: '+',
    79: '1', 80: '2', 81: '3',
    82: '0',          83: '.', 96: 'ENTER'
}

# mapping from characters printed on keys to desired meanings
ORIG_TO_NEW = {
                 '=': '7',      '/': '4',   '*': '1',
    '7': '0',    '8': '8',      '9': '5',   '-': '2',
    '4': 'NEXT', '5': '9',      '6': '6',   '+': '3',
    '1': 'SKIP', '2': 'REWIND', '3': 'VOL-',
    '0': 'PAUSE',               '.': 'VOL+', 'ENTER': 'QUEUE'
}

def decode_key(event):
    """Given a key event, return the translated key_code."""
    return ORIG_TO_NEW.get(CODE_TO_ORIG.get(event.code))
