# Phòng thử nghiệm tương tác mô phỏng

> Ứng dụng Python chạy nội bộ để nghiên cứu chuỗi **Google giả lập → TikTok giả lập** trên dữ liệu tổng hợp. Hộp thư dùng miền `.test`; tài khoản, nhà sáng tạo, video, lượt xem, lượt thích và lượt theo dõi đều là bản ghi giả lập trong SQLite. Máy chủ chỉ bind vào `127.0.0.1`. Mã nguồn không có client email, API mạng xã hội, proxy hoặc chức năng đăng nhập nền tảng thật.

## Tính năng

- Dashboard tiếng Việt có thống kê email, hồ sơ, lượt xem, lượt thích, lượt theo dõi và biểu đồ tích lũy.
- Mỗi tài khoản thử nghiệm đi qua các bước tạo hộp thư Google giả, xác minh thư nội bộ, tạo hồ sơ TikTok giả và mô phỏng xem/thích/theo dõi video tổng hợp.
- Tìm kiếm, lọc, phân trang danh sách tài khoản; mở hộp thư giả lập; tải CSV sự kiện/tài khoản và báo cáo JSON.
- Tạm dừng, tiếp tục từ checkpoint, dừng giữ dữ liệu từng phần, hoặc chạy lại cùng cấu hình và seed.
- So sánh hai lần chạy đã hoàn tất, chênh lệch chỉ số và dấu vân tay kết quả để kiểm tra tính lặp lại.
- Seed cố định cho kết quả xác định. Mọi lần chạy được tách riêng; dữ liệu được lưu bền vững trong SQLite.
- Không dùng framework hoặc thư viện Python bên thứ ba; chỉ cần Python 3.10 trở lên.

## Cấu trúc dự án

```text
social-simulation-lab/
├── lab.py                 # Mô hình, SQLite, quy trình và thống kê
├── server.py              # HTTP loopback, API cục bộ và worker
├── cli.py                 # Chạy mô phỏng từ dòng lệnh
├── static/
│   ├── index.html         # Giao diện dashboard
│   ├── app.js             # Giao tiếp API và biểu đồ
│   └── styles.css         # Giao diện responsive
├── tests/
│   ├── test_lab.py        # Kiểm thử mô hình và tính lặp lại
│   └── test_server.py     # Kiểm thử HTTP loopback/CSRF
├── .gitignore
└── README.md
```

## Chạy trên Windows

Mở PowerShell tại thư mục dự án:

```powershell
cd D:\TTNT\social-simulation-lab
python --version
python server.py
```

Mở `http://127.0.0.1:8765` trên trình duyệt. Máy chủ chỉ nghe trên máy hiện tại; dừng bằng `Ctrl+C`. Cơ sở dữ liệu được tạo tại `data\simulation.sqlite3`.

1. Chọn **Tạo thử nghiệm**, nhập seed và quy mô dữ liệu, rồi bấm **Bắt đầu mô phỏng**.
2. Theo dõi tiến trình và số liệu tại **Tổng quan**. Worker lưu trạng thái sau từng bước.
3. Mở **Tài khoản giả lập** để tìm/lọc hồ sơ TikTok mô phỏng hoặc xem hộp thư Google nội bộ. Mọi địa chỉ kết thúc bằng `mail.test`.
4. Dùng **Tạm dừng** và **Tiếp tục** để thử checkpoint; dùng **Chạy lại** để tạo lần chạy mới với cùng seed.
5. Hoàn tất hai lần chạy rồi mở **So sánh thử nghiệm**. Cùng cấu hình và seed sẽ có cùng dấu vân tay kết quả.
6. Dùng các nút CSV/JSON để xuất dữ liệu tổng hợp.

## Chạy từ dòng lệnh

Lệnh sau tạo thử nghiệm với 30 tài khoản, seed 17, rồi in báo cáo JSON:

```powershell
python cli.py run --name "Kiểm tra seed 17" --seed 17 --accounts 30 --creators 4 --videos-per-creator 3 --views-per-account 2
```

Ghi báo cáo ra tệp:

```powershell
python cli.py run --seed 17 --accounts 30 --output reports\seed-17.json
```

Có thể đổi `--data-dir` để tách cơ sở dữ liệu CLI khỏi dashboard. Chạy lệnh với cùng seed và cấu hình sẽ tái tạo kết quả tương tác; dấu thời gian và UUID của lần chạy vẫn riêng biệt.

## Kiểm thử

```powershell
python -m unittest discover -s tests -v
```

Các kiểm thử chỉ dùng thư mục tạm và dữ liệu giả, không cần Internet.

## Giới hạn mô hình

- Đây là mô hình thực nghiệm nhỏ, không phản ánh thuật toán phân phối, bộ lọc spam hay quy tắc của bất kỳ nền tảng bên ngoài nào.
- Lượt xem có thể lặp trên cùng video; lượt thích được giới hạn một lần trên mỗi cặp tài khoản/video; theo dõi một lần trên mỗi cặp tài khoản/nhà sáng tạo.
- Seed đảm bảo hành vi mô phỏng tái lập; thời gian chạy, timestamp và UUID của lần chạy không dùng để so sánh.
- Không nhập URL, token, cookie, mật khẩu hay thông tin tài khoản bên ngoài. API chỉ chấp nhận cấu hình mô phỏng đã định nghĩa.
