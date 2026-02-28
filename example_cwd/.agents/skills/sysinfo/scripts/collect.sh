#!/bin/bash
# 收集系统信息

echo "=== System Info ==="
echo "OS: $(uname -s) $(uname -r)"
echo "Hostname: $(hostname)"
echo "Arch: $(uname -m)"

echo ""
echo "=== CPU ==="
if [ -f /proc/cpuinfo ]; then
    grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | xargs echo "Model:"
    echo "Cores: $(nproc)"
else
    echo "Model: $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
    echo "Cores: $(sysctl -n hw.ncpu 2>/dev/null || echo unknown)"
fi

echo ""
echo "=== Memory ==="
free -h 2>/dev/null | awk '/^Mem:/{print "Total: "$2, "Used: "$3, "Available: "$7}' || \
    echo "Total: $(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.1f GB", $1/1073741824}' || echo unknown)"

echo ""
echo "=== Disk ==="
df -h / 2>/dev/null | awk 'NR==2{print "Total: "$2, "Used: "$3, "Available: "$4, "Use%: "$5}'

echo ""
echo "=== Python ==="
python3 --version 2>/dev/null || python --version 2>/dev/null || echo "Python: not found"
