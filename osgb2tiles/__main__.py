"""允许通过 python -m osgb2tiles 运行。

子命令：
    python -m osgb2tiles -i ... -o ...     # 默认：OSGB 转 3D Tiles
    python -m osgb2tiles merge -i ... -o ... # 多工程合并
"""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "merge":
    from .merge_tool import main as merge_main
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    merge_main()
else:
    from .cli import main
    main()
