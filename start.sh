#!/usr/bin/env bash
# Chipyard GUI Startup Script

set -e

CHIPYARD_DIR="${CHIPYARD_ROOT:-/home/raphael/chipyard}"
GUI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo "Chipyard GUI Startup"
echo "========================================"

# Check if Chipyard env exists
if [ ! -f "$CHIPYARD_DIR/env.sh" ]; then
    echo "ERROR: Chipyard env.sh not found at $CHIPYARD_DIR/env.sh"
    echo "Please run ./build-setup.sh in chipyard first"
    exit 1
fi

# Chipyard environment laden.
# env.sh setzt conda voraus -- wenn conda nicht im PATH ist, bricht es mit
# "return 1" ab und wuerde das Script hier (wegen set -e) beenden. Deshalb
# conda zuerst explizit bekannt machen.
if ! type conda >/dev/null 2>&1; then
    for _cand in "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3" \
                 "/opt/conda"; do
        if [ -f "$_cand/etc/profile.d/conda.sh" ]; then
            # shellcheck disable=SC1090
            source "$_cand/etc/profile.d/conda.sh"
            break
        fi
    done
fi

echo "Sourcing Chipyard environment..."
# shellcheck disable=SC1090
source "$CHIPYARD_DIR/env.sh"

# Check if conda env is activated
if [ -z "$CONDA_DEFAULT_ENV" ]; then
    echo "WARNING: No conda environment detected"
    echo "Attempting to activate..."
    conda activate "$CHIPYARD_DIR/.conda-env" 2>/dev/null || true
fi

# Check for verilator
if ! command -v verilator &> /dev/null; then
    echo "WARNING: verilator not in PATH"
    echo "Make sure Chipyard setup completed successfully"
fi

# Check for firtool
if ! command -v firtool &> /dev/null; then
    echo "WARNING: firtool not in PATH"
fi

# Install Python dependencies if needed
cd "$GUI_DIR"
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

echo "Activating Python venv..."
source venv/bin/activate

echo "Installing/updating Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Create outputs directory
mkdir -p outputs

echo "========================================"
echo "Starting Chipyard GUI on http://localhost:5000"
echo "========================================"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Run Flask app
export FLASK_APP=app.py
export FLASK_ENV=development
python app.py