#!/bin/zsh
# 双击此文件即可更新导航。移动整个目录后仍可使用。
script_dir="${0:A:h}"
if command -v python3 >/dev/null 2>&1; then
  python3 "$script_dir/update_navigation.py"
  result=$?
else
  print -u2 '未找到 Python 3，请安装 Python 3 后再运行。'
  result=1
fi
if [[ -t 0 ]]; then
  read -r '?按回车关闭窗口…'
fi
exit "$result"
