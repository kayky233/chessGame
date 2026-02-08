"""
Flask API for Xiangqi (Chinese Chess).

Stateless design: no game state stored in memory between requests.
The frontend sends the full board state with every request.
Designed to run behind Gunicorn with multiple workers.
"""

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from engine import XiangqiEngine
import time

app = Flask(__name__)
CORS(app)


@app.route('/')
def index():
    """Serve the frontend."""
    return render_template('index.html')


@app.route('/ai-move', methods=['POST'])
def ai_move():
    """
    AI move endpoint.
    Input:  JSON { "board": [[...], ...] }  (10x9 matrix)
    Output: JSON { "status": "ok", "move": {"from": [r,c], "to": [r,c]}, "time": float }
    """
    data = request.get_json(silent=True)
    if not data or 'board' not in data:
        return jsonify({
            'status': 'error',
            'message': 'Missing board data'
        }), 400

    board = data['board']

    # Validate board dimensions
    if not isinstance(board, list) or len(board) != 10:
        return jsonify({
            'status': 'error',
            'message': 'Board must be a 10-row array'
        }), 400

    for i, row in enumerate(board):
        if not isinstance(row, list) or len(row) != 9:
            return jsonify({
                'status': 'error',
                'message': f'Row {i} must have 9 columns'
            }), 400

    # Create engine per request (stateless)
    depth = data.get('depth', 4)
    depth = max(1, min(depth, 6))  # Clamp to safe range
    engine = XiangqiEngine(depth=depth)

    t0 = time.time()
    move = engine.get_best_move(board)
    dt = time.time() - t0

    if move is None:
        return jsonify({
            'status': 'ok',
            'move': None,
            'message': 'No valid moves for AI',
            'time': round(dt, 3)
        })

    fr, fc, tr, tc = move
    return jsonify({
        'status': 'ok',
        'move': {
            'from': [fr, fc],
            'to': [tr, tc]
        },
        'time': round(dt, 3),
        'nodes': engine.nodes
    })


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
