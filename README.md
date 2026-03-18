# Early Support

とりあえず早朝対応の日程一覧を表示するだけのページ<br>
他ツールとの連携は今のところあんまり考えてない<br>
<br>
# 構成
下記によって、早朝対応を自動で表示するページを作成する<br><br>
受付管理表 VBAで早朝対応時にREST APIを叩く<br>
本リポジトリの./public/log.txtに早朝対応日時が追記される<br>
./index.htmlで./public/log.txtの内容を読み込む
