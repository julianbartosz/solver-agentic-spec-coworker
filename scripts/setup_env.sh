#!/bin/bash
# =============================================================================
# Environment Setup Script for Integration Coworker
# =============================================================================
# This script ensures a consistent, working Python environment with compatible
# package versions. It prevents the "broken venv" issues caused by version drift.
#
# Usage:
#   ./scripts/setup_env.sh           # Create/update environment
#   ./scripts/setup_env.sh --check   # Validate existing environment
#   ./scripts/setup_env.sh --clean   # Remove and recreate environment
# =============================================================================

set -e  # Exit on error

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_NAME=".venv311"
VENV_PATH="$PROJECT_ROOT/$VENV_NAME"
REQUIRED_PYTHON_VERSION="3.11"
LOCK_FILE="$PROJECT_ROOT/requirements.lock.txt"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements.txt"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# =============================================================================
# Find Python 3.11
# =============================================================================
find_python311() {
    # Try common locations for Python 3.11
    local candidates=(
        "python3.11"
        "/opt/homebrew/bin/python3.11"
        "/usr/local/bin/python3.11"
        "/usr/bin/python3.11"
    )
    
    for candidate in "${candidates[@]}"; do
        if command -v "$candidate" &> /dev/null; then
            version=$("$candidate" --version 2>&1 | grep -oE '3\.11\.[0-9]+')
            if [[ -n "$version" ]]; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    
    return 1
}

# =============================================================================
# Validate Environment
# =============================================================================
validate_environment() {
    log_info "Validating environment..."
    local errors=0
    
    # Check venv exists
    if [[ ! -d "$VENV_PATH" ]]; then
        log_error "Virtual environment not found at $VENV_PATH"
        return 1
    fi
    
    # Check Python version in venv
    local venv_python="$VENV_PATH/bin/python"
    if [[ ! -f "$venv_python" ]]; then
        log_error "Python executable not found in venv"
        return 1
    fi
    
    local py_version=$("$venv_python" --version 2>&1)
    if [[ ! "$py_version" =~ "3.11" ]]; then
        log_error "Wrong Python version: $py_version (expected 3.11.x)"
        ((errors++))
    else
        log_success "Python version: $py_version"
    fi
    
    # Check for mixed site-packages (the bug we encountered)
    local site_packages_count=$(ls -d "$VENV_PATH/lib/"python* 2>/dev/null | wc -l)
    if [[ $site_packages_count -gt 1 ]]; then
        log_error "Multiple Python versions in site-packages! This causes package conflicts."
        log_error "Found: $(ls -d "$VENV_PATH/lib/"python* 2>/dev/null | tr '\n' ' ')"
        ((errors++))
    else
        log_success "Single Python version in site-packages"
    fi
    
    # Validate critical packages
    log_info "Checking critical packages..."
    local critical_packages=(
        "langgraph:1.0"
        "langgraph-checkpoint:3.0"
        "langgraph-checkpoint-postgres:3.0"
        "langchain:1.1"
        "langchain-core:1.1"
        "psycopg:3.3"
    )
    
    for pkg_spec in "${critical_packages[@]}"; do
        local pkg_name="${pkg_spec%%:*}"
        local expected_prefix="${pkg_spec##*:}"
        
        local installed=$("$venv_python" -c "import importlib.metadata; print(importlib.metadata.version('$pkg_name'))" 2>/dev/null || echo "NOT_INSTALLED")
        
        if [[ "$installed" == "NOT_INSTALLED" ]]; then
            log_error "$pkg_name: NOT INSTALLED"
            ((errors++))
        elif [[ ! "$installed" =~ ^$expected_prefix ]]; then
            log_warn "$pkg_name: $installed (expected $expected_prefix.x)"
        else
            log_success "$pkg_name: $installed"
        fi
    done
    
    # Test critical imports
    log_info "Testing critical imports..."
    if "$venv_python" -c "
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langchain_openai import ChatOpenAI
print('All imports successful')
" 2>/dev/null; then
        log_success "All critical imports work"
    else
        log_error "Critical imports failed!"
        ((errors++))
    fi
    
    if [[ $errors -gt 0 ]]; then
        log_error "Validation failed with $errors error(s)"
        return 1
    fi
    
    log_success "Environment validation passed!"
    return 0
}

# =============================================================================
# Create Environment
# =============================================================================
create_environment() {
    log_info "Creating virtual environment..."
    
    # Find Python 3.11
    local python_bin
    python_bin=$(find_python311) || {
        log_error "Python 3.11 not found. Please install it:"
        log_error "  macOS: brew install python@3.11"
        log_error "  Ubuntu: sudo apt install python3.11 python3.11-venv"
        exit 1
    }
    
    log_info "Using Python: $python_bin ($($python_bin --version))"
    
    # Create venv
    log_info "Creating venv at $VENV_PATH..."
    "$python_bin" -m venv "$VENV_PATH"
    
    # Upgrade pip
    log_info "Upgrading pip..."
    "$VENV_PATH/bin/pip" install --upgrade pip wheel setuptools
    
    # Install from lock file if available, otherwise from requirements.txt
    if [[ -f "$LOCK_FILE" ]]; then
        log_info "Installing from requirements.lock.txt (exact versions)..."
        "$VENV_PATH/bin/pip" install -r "$LOCK_FILE"
    elif [[ -f "$REQUIREMENTS_FILE" ]]; then
        log_warn "No lock file found, installing from requirements.txt (versions may vary)"
        "$VENV_PATH/bin/pip" install -r "$REQUIREMENTS_FILE"
        
        # Generate lock file for next time
        log_info "Generating requirements.lock.txt..."
        "$VENV_PATH/bin/pip" freeze > "$LOCK_FILE"
    else
        log_error "No requirements file found!"
        exit 1
    fi
    
    # Install package in editable mode
    log_info "Installing package in editable mode..."
    "$VENV_PATH/bin/pip" install -e "$PROJECT_ROOT"
    
    log_success "Environment created successfully!"
}

# =============================================================================
# Clean Environment
# =============================================================================
clean_environment() {
    log_warn "This will remove the existing virtual environment at $VENV_PATH"
    read -p "Are you sure? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_info "Removing $VENV_PATH..."
        rm -rf "$VENV_PATH"
        log_success "Environment removed"
    else
        log_info "Cancelled"
        exit 0
    fi
}

# =============================================================================
# Print Usage
# =============================================================================
print_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Options:
  (no args)    Create or update virtual environment
  --check      Validate existing environment
  --clean      Remove and recreate environment
  --help       Show this help message

Environment Details:
  Python:      3.11.x required
  Venv:        $VENV_NAME
  Lock file:   requirements.lock.txt

Examples:
  # First time setup
  ./scripts/setup_env.sh
  
  # Check if environment is healthy
  ./scripts/setup_env.sh --check
  
  # Fix a broken environment
  ./scripts/setup_env.sh --clean
  ./scripts/setup_env.sh
EOF
}

# =============================================================================
# Main
# =============================================================================
main() {
    cd "$PROJECT_ROOT"
    
    case "${1:-}" in
        --check)
            validate_environment
            ;;
        --clean)
            clean_environment
            create_environment
            validate_environment
            ;;
        --help|-h)
            print_usage
            ;;
        "")
            if [[ -d "$VENV_PATH" ]]; then
                log_info "Environment exists, validating..."
                if validate_environment; then
                    log_success "Environment is healthy!"
                else
                    log_warn "Environment has issues. Run with --clean to recreate."
                    exit 1
                fi
            else
                create_environment
                validate_environment
            fi
            ;;
        *)
            log_error "Unknown option: $1"
            print_usage
            exit 1
            ;;
    esac
    
    echo ""
    log_info "To activate the environment:"
    echo "    source $VENV_NAME/bin/activate"
    echo ""
    log_info "Or run commands directly:"
    echo "    $VENV_NAME/bin/python your_script.py"
}

main "$@"
