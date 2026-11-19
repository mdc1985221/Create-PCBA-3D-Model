# 离线 DFN 建模演示模板

`dfn12.json` 是人工填写的合成参数，代表 3×2×0.75 mm、0.5 mm 脚距的通用 DFN12；标记为 `DEMO12`，不是任何厂商或实际板的型号。端子和散热焊盘使用简化矩形，不是原厂精确封装。

在仓库根目录运行（使用已安装 requirements.txt 的 Python）：

```powershell
python scripts/create_dfn_demo.py --home D:\PCBA3D_Demo
```

该命令不联网，不调用 API；先核对规格书并填写 JSON。它生成黑色本体、实体凹刻文字与第一脚标记，以及包含原生 STEP 的预览草稿。输出 JSON 给出草稿编号；预览文件位于 `D:\PCBA3D_Demo\模型草稿\<编号>\index.html`。浏览器页面需通过工具的本地服务打开，不能直接双击 HTML。

把 `--home` 指向实际 EXE 所在的资料目录时，运行工具后可访问 `http://127.0.0.1:<本次端口>/model-preview/<编号>/index.html`。先确认顶面第一脚、底面端子、贴板面、尺寸和方向；随后使用相同参数加 `--save` 创建新草稿并保存入用户库。演示名已存在时会报错，请复用或更改名称。

```powershell
python scripts/create_dfn_demo.py --home D:\PCBA3D_Demo --save
```

保存后从工具的模型选择器绑定真实封装，点击“应用绑定并刷新 3D”，检查各面各角度的实际焊盘与引脚，保存校准绑定，然后重新导出 STEP。草稿只存在于本地，不随 Git 上传。

这是一份可修改的源码演示脚本，尚无对应的一键界面或 PDF 自动识别功能；预编译 EXE 的 BGA 生成、STEP 导入与 API 参数草稿是另外的功能。尺寸未知、照片批次模糊或第一脚冲突时应停留在待确认状态，不能通过猜字符/猜角位补全。
