# 邮史展品

青槐邮史室整理小型借展的邮封文字。实物身份、邮局、邮路及历史日期为虚构。

## 资料格式

数据位于 `data/records.json`，以 UTF-8 保存。各表的记录以本表内的 `id` 或 `seq` 识别。原话保留提交时的文字。

每件封有自己独立的 front、back、insert 分面。marks 的 order_hint 为馆员按叠印推得的可见先后，空值代表未辨清。readings 的 mark 是同一物理戳迹，原文保留各抄录的争议。开放时段含首尾日期。routes.min_hours 为通达下界，通路标示普通件或退件。时代对照采用馆内已核准的合成年表。

- `covers` 保存封件与展签现用名称。 字段为 `id`、`image`、`insert`、`note`。
- `offices` 保存邮局、历史简称和局址有效期。 字段为 `id`、`name`、`aliases`、`open`、`close`。
- `era` 保存馆内年代对照与封件启用信息。 字段为 `id`、`text`。
- `routes` 保存开放通路和最短通达时间。 字段为 `id`、`origin`、`destination`、`min_hours`、`direction`、`open`、`close`。
- `marks` 保存同一邮封各面上的戳迹及可见的大致次序。 字段为 `id`、`cover`、`side`、`order_hint`。
- `readings` 保存多位抄录人的原始释读。 字段为 `id`、`mark`、`reader`、`text`、`certainty`。
- `display` 保存馆方借展组合及说明草稿。 字段为 `id`、`covers`、`time`、`draft`。

## 查看资料与自检

使用 Python 3.11.2 自带的标准库读取资料并验证字段和引用。

```sh
python3 -m json.tool data/records.json
python3 -m unittest discover -s tests -v
```
