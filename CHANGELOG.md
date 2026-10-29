# Changelog

Version của tool nằm ở `eval_agent/__version__`, kiểm bằng `eval-agent --version`. Mỗi run lưu kèm version này, nên `batches` và `compare` biết một batch được chấm bằng bản nào.

## 0.3.0 — 2026-10-29

- **Versioning.** Thêm các lệnh `rubric versions`, `rubric new-version`, `rubric diff`, `batches`, `compare`, và `calibrate try` (kiểm bản nháp rubric so với nhãn tham chiếu trước khi duyệt).
  - Version mới chỉ thành active khi được duyệt ở CP3. Bản cũ vẫn chấm được trong lúc chờ, duyệt xong mới bị archive.
  - Mỗi run lưu `tool_version`, `rubric_hash`, `judge_prompt_hash`.
  - Report của `analyze` và nhật ký agent ghi lại version của prompt.
  - `compare` cảnh báo khi hai batch được chấm bằng rubric hoặc judge khác nhau.
- **Chế độ agent** (`eval-agent agent`): LLM tự chọn tool trong một danh sách được phép, có giới hạn chi phí và nhật ký phiên. Không có tool nào vượt được checkpoint.
- **Độ tin cậy của judge:**
  - `stability` chấm lại cùng transcript nhiều lần để đo nhiễu của judge.
  - `calibrate export / import / report` đối chiếu judge với người chấm (MAE, ±2, bias, Spearman), phiếu chấm tay không hiện điểm judge.
- **Judge:** một lần chấm hỏng không còn làm mất cả item.

## 0.2.0 — 2026-10-29

- **Đóng gói:** cài bằng `pip`, lệnh `eval-agent`, thư mục làm việc `EVAL_AGENT_HOME`, lệnh `init`, file `.env.example`.
- **Checkpoint:**
  - CP1–CP3 (scope, AI spec, rubric) qua lệnh `approve`.
  - `eval` / `run` bị chặn khi rubric chưa duyệt (`--allow-draft` cho chạy thử, không lưu).
  - Quyết định baseline theo batch.
  - CP4: `analyze` → `report approve` → `report send`, kèm kiểm tra hash nội dung report.
- **`status`:** cho biết việc gì đang chờ người.
- **Adapter `refyn-plan`** cho ISI213, lệnh `run`.

## 0.1.0

- Pipeline PO spec → AI spec → rubric, LLM-as-judge có kiểm câu trích, lưu kết quả vào SQLite, baseline và phát hiện regression.
