"""Original code-comment samples plus explicitly reconstructed message fields.

No saved Telegram logs existed in the supplied repository. The original names
'Player' and 'magic' below are preserved; their opponents and trade statistics
are synthetic. These are regression fixtures, not claimed historical trades.
"""


def message(market, side, price='0.50', win_rate=75, roi=75):
    return (f'📌 {market}\nSampleTrader bought {side} for $1,250\n'
            f'Entry price: ${price}\nWin rate: {win_rate}%\nROI: {roi}%')


SAMPLES = [
    ('Spread: Ohio State (-27.5)', 'Ohio State', 'spread', 'Ohio State', None, -27.5),
    ('Spread: Ohio State (-27.5)', 'Marshall', 'spread', 'Ohio State', 'Marshall', -27.5),
    ('Falcons vs Packers: O/U 40.5', 'Under', 'total', 'Falcons', 'Packers', 40.5),
    ('Counter-Strike: magic vs Example Team (BO3)', 'magic', 'game_winner', 'magic', 'Example Team', None),
    ('Buenos Aires 2: Player vs Example Opponent', 'Player', 'game_winner', 'Player', 'Example Opponent', None),
]
