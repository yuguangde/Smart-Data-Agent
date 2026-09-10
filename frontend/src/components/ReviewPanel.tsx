/**
 * ReviewPanel — side drawer that shows the review-agent report and the
 * structured comparison result.
 */
import { LoadingOutlined } from "@ant-design/icons";
import { Alert, Badge, Card, Drawer, Spin, Typography } from "antd";

import { ChartMarkdown } from "@/components/ChartRenderer";
import type { ReviewStore } from "@/store/useReviewStore";

const { Paragraph, Text } = Typography;

function severityColor(severity: string): string {
  switch (severity) {
    case "major":
      return "red";
    case "minor":
      return "orange";
    case "cosmetic":
    default:
      return "blue";
  }
}

function verdictColor(verdict: string): string {
  switch (verdict) {
    case "consistent":
      return "success";
    case "inconsistent":
      return "error";
    case "partial":
    default:
      return "warning";
  }
}

function verdictText(verdict: string): string {
  switch (verdict) {
    case "consistent":
      return "一致";
    case "inconsistent":
      return "不一致";
    case "partial":
    default:
      return "部分一致";
  }
}

export interface ReviewPanelProps extends ReviewStore {
  onClose: () => void;
}

export function ReviewPanel({
  visible,
  loading,
  reviewReport,
  reviewThreadId,
  comparison,
  error,
  onClose,
}: ReviewPanelProps) {
  return (
    <Drawer
      title={
        <span>
          报告复核
          {reviewThreadId && (
            <Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>
              {reviewThreadId}
            </Text>
          )}
        </span>
      }
      placement="right"
      width={900}
      onClose={onClose}
      open={visible}
    >
      {error && (
        <Alert message="复核失败" description={error} type="error" showIcon closable />
      )}

      {comparison && (
        <Alert
          message={
            <span>
              复核结论：
              <Badge
                status={verdictColor(comparison.verdict) as any}
                text={verdictText(comparison.verdict)}
                style={{ marginLeft: 8 }}
              />
            </span>
          }
          description={comparison.summary}
          type={verdictColor(comparison.verdict) as any}
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {comparison && comparison.differences.length > 0 && (
        <Card title="差异点" size="small" style={{ marginBottom: 16 }}>
          {comparison.differences.map((d, idx) => (
            <div key={idx} style={{ marginBottom: 12, paddingBottom: 12, borderBottom: "1px solid #f0f0f0" }}>
              <div>
                <Text strong>{d.aspect}</Text>{" "}
                <Badge color={severityColor(d.severity)} text={d.severity} />
              </div>
              <Paragraph type="secondary" style={{ margin: 0 }}>
                复核报告：{d.review || "—"}
              </Paragraph>
            </div>
          ))}
        </Card>
      )}

      <Card
        title={
          <span>
            复核报告{" "}
            {loading && <Spin indicator={<LoadingOutlined spin />} size="small" />}
          </span>
        }
        size="small"
        bodyStyle={{ maxHeight: 480, overflow: "auto" }}
      >
        <div className="markdown-body">
          {reviewReport ? (
            <ChartMarkdown content={reviewReport} />
          ) : (
            <Text type="secondary">{loading ? "复核中…" : "暂无复核报告"}</Text>
          )}
        </div>
      </Card>
    </Drawer>
  );
}
