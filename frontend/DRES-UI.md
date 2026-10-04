# UI nộp DRES trên Mentos

Thanh **DRES** nằm trên đầu màn Visual Search. Search, CSV và player giữ nguyên. Việc nộp bài không copy JSON tay: UI tự đổi frame sang millisecond, ghép đúng format, rồi gọi DRES.

Trình duyệt không gọi `eventretrieval.one` trực tiếp. Mọi request đi qua `POST /dres-proxy` trên API Mentos (tránh CORS). Backend phải là bản có route này.

## Chốt đáp án rồi bấm là nộp

Có, với **Textual KIS**, **Video KIS** và **Q&A**.

1. Một lần đầu buổi thi: mở **Chi tiết**, điền server / username / password, bấm **Login + evaluation ACTIVE**. Tài khoản được nhớ trong tab. `sessionId` và evaluation **không** hard-code; mỗi lần nộp hệ thống login lại nếu cần và lấy evaluation `ACTIVE`.
2. Chọn loại câu trên thanh DRES. Khi BTC mở đề, bấm **Câu mới** để đếm giờ và đưa số lần sai `k` về 0.
3. Search như bình thường. Trên frame muốn nộp, bấm **Nộp … ms**. Một cú bấm đó là cả video lẫn mốc: UI tự gọi API.

Cùng tác dụng với **Nộp DRES** trên thanh, hoặc **Alt+S**, nếu mốc đã được chọn. Trong player, **Nộp mốc đang phát** nộp đúng khung đang xem (nên dùng khi muốn frame giữa đoạn, không lấy mép kết quả).

**TRAKE** khác một nhịp. Nút trên frame chỉ **thêm** frame vào danh sách, đúng thứ tự bấm. Khi chuỗi đủ, bấm **Nộp DRES** (hoặc Alt+S) thì mới gọi API. Trong player có **List pick → TRAKE** để đổ các keyframe đã pick sang danh sách đó.

**Dry-run payload** chỉ hiện JSON, không gửi.

Nộp bị chặn nếu cùng câu đã gửi đúng kết quả đó. Muốn gửi lại phải bấm **Vẫn nộp trùng**.

Thiếu `fps` trong metadata thì nút tắt (`fps?`). UI không đoán fps.

## Ba format gửi đi

Millisecond: `ms = round(frame / fps × 1000)`. Tên video là id, bỏ đuôi `.mp4` nếu có.

| Loại câu | Cửa sổ | Body |
|---|---|---|
| Textual KIS, Video KIS | 5′ / 4′ | `{ mediaItemName, start, end }` tính bằng ms. Mặc định `start = end`. Ô **Kéo dài end** cộng thêm ms vào `end`. |
| Q&A | 5′ | `QA-<đáp án>-<video>-<ms>`. Đáp án gõ ở **Chi tiết**, không được chứa dấu `-`. |
| TRAKE | 5′ | `TR-<video>-<frame1>,<frame2>,...` — frame id, đúng thứ tự, một video. |

API thực tế:

1. `POST /api/v2/login` → `sessionId`
2. `GET /api/v2/client/evaluation/list?session=` → evaluation `ACTIVE`
3. `POST /api/v2/submit/{evaluationId}?session=` với body `answerSets`

HTTP 401 thì login lại một lần rồi nộp lại.

## Thanh trên cùng

- Loại câu
- Thời gian còn lại (chưa bấm **Câu mới** thì hiện cả cửa sổ)
- Điểm **nếu đúng ngay lúc này**: 100 lúc mở câu, về 50 hết giờ, mỗi lần sai trừ 10. TRAKE đúng một phần được ước lượng một nửa.
- `k`: số lần sai trước lần đúng đầu. DRES trả `WRONG` thì `k` tự tăng. Có nút **+1 lần sai** nếu muốn tự cộng.
- Dòng xem trước đúng chuỗi sẽ gửi
- **Câu mới**, **Nộp DRES**, **Chi tiết**

Gợi ý dưới thanh: còn dưới 1 phút thì nhắc nộp nếu đã chắc; hit top ≤ 20 thì nhắc lấy frame giữa đoạn.

## Chi tiết

- Server (mặc định `https://eventretrieval.one`), username, password
- Checklist 30 giây đầu câu (Watcher đọc đề, không chụp clip Video KIS; Operator search tiếng Việt; SigLIP2 + Translate; TRAKE thì Temporal)
- Video KIS: bốn ô mô tả (bối cảnh → vật / màu / logo → hành động → chữ OCR) và **Đưa vào ô search**
- Q&A: ô đáp án
- TRAKE: danh sách frame, nút lên / xuống / xóa
- **Results 100 / 1000** và **Bật OCR / OD** chỉ đổi control search, vẫn phải bấm Search
- Dry-run và payload JSON của lần dựng đáp án gần nhất

## Search đi kèm, không đổi cách search cũ

- Model mặc định SigLIP2, **Translate bật** để gõ tiếng Việt.
- Chọn TRAKE thì Temporal bật, Hybrid tắt.
- CSV, filter, ASR / OD / OCR vẫn dùng như trước.
