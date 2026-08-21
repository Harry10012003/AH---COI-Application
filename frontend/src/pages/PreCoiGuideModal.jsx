import { useEffect, useRef } from 'react'

export default function PreCoiGuideModal({ onClose }) {
  const closeRef = useRef(null)

  useEffect(() => {
    closeRef.current?.focus()
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <div className="precoi-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="precoi-guide-modal" role="dialog" aria-modal="true" aria-labelledby="precoi-guide-title">
        <header className="precoi-guide-header">
          <div>
            <p className="eyebrow">PRE-COI GUIDE</p>
            <h2 id="precoi-guide-title">Hướng Dẫn Pre-COI</h2>
            <p>Quy trình tạo, kiểm tra, cập nhật và tải file Pre-COI trên web.</p>
          </div>
          <button ref={closeRef} className="btn" onClick={onClose}>Close</button>
        </header>

        <div className="precoi-guide-content">
          <h2>1. Mục đích</h2>
          <p>App tạo và cập nhật Pre-COI từ GO / Batch GO, YPD, MES, PPO report và AWS SQL. Kết quả có hai tab: <strong>COI</strong> và <strong>COI Collar/Cuff</strong>.</p>

          <h2>2. Quy trình chuẩn</h2>
          <h3>Bước 1 — Nhập GO / Batch GO</h3>
          <p>Nhập mỗi GO một dòng hoặc ngăn cách bằng dấu phẩy / chấm phẩy. Có thể trộn Knit và Woven.</p>

          <h3>Bước 2 — Nhập Account ESCM</h3>
          <p>Nhập Account và Password ESCM có quyền YPD. Chỉ tick <strong>Remember account/password</strong> trên Windows account cá nhân; thông tin được lưu trong browser profile hiện tại.</p>

          <h3>Bước 3 — Create Output</h3>
          <p>Bấm <strong>Create Output</strong>. App lấy dữ liệu nguồn và mở table review khi hoàn tất; không cần mở hoặc upload workbook trong Excel.</p>

          <h3>Bước 4 — Review & Input PPO</h3>
          <ul>
            <li>Chuyển giữa hai tab COI và COI Collar/Cuff để kiểm tra kết quả.</li>
            <li>Nhập PPO hoặc YY Req No trực tiếp. Có thể paste một cột từ Excel.</li>
            <li>Nhiều PPO: dùng <code>PPO1, PPO2, PPO3</code>. Vị trí không có PPO vẫn giữ dấu phẩy, ví dụ <code>PPO1,,PPO3</code>.</li>
            <li>Dùng phím <strong>↑ / ↓</strong> để di chuyển lên/xuống trong cùng cột, thuận tiện khi nhập hoặc paste dữ liệu.</li>
            <li>Nhấp đúp ô vuông ở góc phải dưới cell để fill tới dòng cuối; kéo ô vuông để fill vùng chọn. Bảng tự scroll ở mép trên/dưới và hiện số dòng sắp fill.</li>
            <li>Dùng <strong>Undo fill</strong> / <strong>Redo fill</strong> để hoàn tác hoặc làm lại nhiều thao tác fill liên tiếp.</li>
          </ul>

          <h3>Bước 5 — Save Draft</h3>
          <p>Bấm <strong>Save Draft</strong> trước khi chạy update. Draft được lưu trong job hiện tại của bạn trên server.</p>

          <h3>Bước 6 — Chạy update cần thiết</h3>
          <ul>
            <li><strong>Update YY Req No</strong>: cập nhật Marker YY từ YPD; cần Account/Password ESCM.</li>
            <li><strong>Update PPO Qty</strong>: dùng PPO đã lưu trong draft để lấy qty / process từ AWS SQL.</li>
            <li><strong>Update CM</strong>: tạo workbook CM từ GO / MES.</li>
          </ul>

          <h3>Bước 7 — Review kết quả &amp; download</h3>
          <p>Sau mỗi update, kiểm tra cả hai tab result. Bấm <strong>OK &amp; Download</strong> hoặc <strong>Download Excel</strong>, rồi chọn folder trong Save As. Tên file là <strong>Pre-COI &lt;GO&gt;.xlsx</strong>.</p>

          <h2>3. Lưu ý &amp; lỗi thường gặp</h2>
          <ul>
            <li>Không có dữ liệu YPD: kiểm tra GO, Account/Password ESCM và quyền YPD.</li>
            <li>PPO Qty trống/sai: kiểm tra format PPO, thứ tự size và dữ liệu AWS SQL.</li>
            <li>Không thấy Save As: dùng Chrome/Edge qua localhost hoặc HTTPS; LAN HTTP sẽ dùng download setting của browser.</li>
            <li>File đang mở trong Excel: đóng file trước khi chạy lại update.</li>
          </ul>
        </div>
      </section>
    </div>
  )
}
