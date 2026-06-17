ここにアプリアイコンを置きます。

1. GPT等で 1024x1024 PNG (背景透過・フラットデザイン・文字なし) を作る
2. icon.ico (複数サイズ内包) に変換して、このフォルダに「icon.ico」という名前で置く
   - 変換は Pillow で可能:
       python -m pip install pillow
       python -c "from PIL import Image; Image.open('icon.png').save('assets/icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
3. build_exe.bat を実行すると、このアイコンが exe とウィンドウに自動で使われる

icon.ico がこのフォルダに無い場合は、アイコン指定なしでビルドされます (動作には影響なし)。
