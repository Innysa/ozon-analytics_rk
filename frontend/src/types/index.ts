export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  is_admin: boolean;
}

export interface Store {
  id: string;
  name: string;
  legal_name: string | null;
  my_role: string;
  last_sync_at: string | null;
}

export interface UserRow {
  id: string;
  email: string;
  full_name: string;
  is_admin: boolean;
  is_active: boolean;
}

export interface Membership {
  id: string;
  user_id: string;
  store_id: string;
  role: "owner" | "manager" | "viewer";
}

export interface Product {
  id: string;
  store_id: string;
  ozon_sku: string;
  ozon_product_id: number | null;
  offer_id: string | null;
  name: string;
  image_url: string | null;
  price_rub: number | null;
  old_price_rub: number | null;
  // Manually entered — Ozon never exposes a seller's own purchase cost via
  // any API. Used to compute margin/ROI on the Dashboard.
  cost_price_rub: number | null;
  fbo_stock: number | null;
  fbs_stock: number | null;
  is_archived: boolean;
}

export interface ReviewAIAnalysis {
  sentiment: "positive" | "neutral" | "negative";
  category: string;
  urgency: "low" | "medium" | "high";
  reply_needed: boolean;
  advantages: string[];
  complaints: string[];
  product_improvements: string[];
  card_improvements: string[];
  hypotheses: string[];
}

export interface ReviewComment {
  id: string;
  text: string;
  status: "draft" | "approved" | "published" | "publish_failed";
  generated_by_ai: boolean;
  edited_by_user: boolean;
  published_via: string | null;
  ozon_comment_id: string | null;
  publish_error: string | null;
  created_at: string;
}

export interface Review {
  id: string;
  store_id: string;
  product_id: string | null;
  product_name: string | null;
  product_sku: string | null;
  product_offer_id: string | null;
  product_image_url: string | null;
  ozon_review_id: string;
  source: string;
  rating: number;
  text: string | null;
  pros: string | null;
  cons: string | null;
  existing_seller_reply: string | null;
  published_at: string | null;
  status: string;
  is_demo: boolean;
  analysis: ReviewAIAnalysis | null;
  latest_draft: ReviewComment | null;
}

export interface ReviewListResponse {
  items: Review[];
  total: number;
}

export interface NamedCount {
  label: string;
  count: number;
  review_ids: string[];
}

export interface RatingBucket {
  rating: number;
  count: number;
}

export interface ReviewAnalytics {
  has_data: boolean;
  total_reviews: number;
  average_rating: number | null;
  low_rating_share: number | null;
  reviews_without_reply: number;
  rating_distribution: RatingBucket[];
  top_advantages: NamedCount[];
  top_complaints: NamedCount[];
  products_with_rising_negativity: string[];
  card_improvement_ideas: NamedCount[];
  product_improvement_ideas: NamedCount[];
  infographic_ideas: NamedCount[];
}

export interface StoreAISettings {
  brand_name: string | null;
  tone_of_voice: string;
  customer_address_form: string;
  reply_length: string;
  use_emoji: boolean;
  signature: string | null;
  forbidden_words: string | null;
  allowed_promises: string | null;
  negative_review_rules: string | null;
  warranty_info: string | null;
  return_policy_info: string | null;
  support_contacts: string | null;
  product_facts: string | null;
}

export interface OzonCredentialsStatus {
  configured: boolean;
  client_id_masked?: string | null;
  api_key_masked?: string | null;
  last_connection_check_at?: string | null;
  last_connection_ok?: boolean | null;
  last_connection_message?: string | null;
  reviews_api_available?: boolean | null;
}

export interface ChangeHistoryEntry {
  id: string;
  store_id: string;
  product_id: string;
  user_id: string | null;
  user_name: string | null;
  change_type: string;
  changed_at: string;
  description: string;
  comment: string | null;
}

export interface ImportSummary {
  fetched: number;
  created: number;
  skipped_duplicate: number;
  errors: string[];
}

export interface SyncRun {
  id: string;
  source_type: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  items_fetched: number;
  items_created: number;
  items_skipped_duplicate: number;
  error_message: string | null;
}

export interface PerformanceCredentialsStatus {
  configured: boolean;
  client_id_masked?: string | null;
  client_secret_masked?: string | null;
  last_connection_check_at?: string | null;
  last_connection_ok?: boolean | null;
  last_connection_message?: string | null;
}

export interface AdvertisingCampaign {
  id: string;
  ozon_campaign_id: string;
  name: string | null;
  campaign_type: string | null;
  state: string | null;
  daily_budget_rub: number | null;
  date_from: string | null;
  date_to: string | null;
}

export interface AdvertisingStatistic {
  id: string;
  product_id: string | null;
  product_name: string | null;
  product_sku: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  ozon_campaign_id: string;
  ad_tool: string | null;
  placement: string | null;
  period_start: string;
  period_end: string;
  spend_rub: number;
  sales_promo_rub: number | null;
  units_sold: number | null;
  impressions: number | null;
  clicks: number | null;
  ctr_pct_ozon: number | null;
  cart_additions: number | null;
  cart_conversion_pct_ozon: number | null;
  drr_promo_pct_ozon: number | null;
  drr_total_pct_ozon: number | null;
  cost_per_order_rub_ozon: number | null;
  avg_cpc_rub_ozon: number | null;
  drr_calculated_pct: number | null;
  roas_calculated: number | null;
}

export interface AdvertisingStatisticListResponse {
  items: AdvertisingStatistic[];
  total: number;
}

// Automatically-collected daily statistics from Ozon Performance API (see
// backend app.services.advertising_daily_sync_service) — genuinely daily,
// unlike AdvertisingStatistic which reflects whatever period a CSV upload
// covers. Deliberately a separate type/endpoint: summing this together with
// AdvertisingStatistic risks double-counting spend/revenue.
export interface AdvertisingDailyStatistic {
  id: string;
  product_id: string | null;
  product_name: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  ozon_campaign_id: string;
  ozon_sku: string;
  date: string;
  product_price_rub: number | null;
  page_type: string | null;
  impression_condition: string | null;
  impressions: number | null;
  clicks: number | null;
  ctr_pct_ozon: number | null;
  cart_additions: number | null;
  avg_bid_rub_ozon: number | null;
  spend_rub: number | null;
  orders: number | null;
  revenue_rub: number | null;
  orders_model: number | null;
  revenue_model_rub: number | null;
  // Ozon's own row-level ДРР, stored as-is (never recomputed) — same rule
  // as elsewhere in the app: aggregating UI shows a calculated ДРР
  // (spend/revenue summed first), not an average of these per-row percents.
  drr_promo_pct_ozon: number | null;
  drr_total_pct_ozon: number | null;
}

export interface AdvertisingDailyStatisticListResponse {
  items: AdvertisingDailyStatistic[];
  total: number;
}

export interface CampaignBreakdown {
  campaign_id: string;
  campaign_name: string;
  spend_rub: number;
  sales_promo_rub: number;
  drr_calculated_pct: number | null;
  roas_calculated: number | null;
}

export interface ProductBreakdown {
  product_id: string;
  product_name: string;
  spend_rub: number;
  sales_promo_rub: number;
  drr_calculated_pct: number | null;
  roas_calculated: number | null;
}

export interface AdvertisingAnalytics {
  has_data: boolean;
  period_start: string | null;
  period_end: string | null;
  total_spend_rub: number;
  total_sales_promo_rub: number;
  total_impressions: number;
  total_clicks: number;
  total_units_sold: number;
  drr_calculated_pct: number | null;
  roas_calculated: number | null;
  ctr_calculated_pct: number | null;
  avg_cpc_calculated_rub: number | null;
  by_campaign: CampaignBreakdown[];
  by_product: ProductBreakdown[];
}

export interface MetricComparison {
  today: number;
  yesterday: number;
  delta: number;
  delta_pct: number | null;
  direction: "up" | "down" | null;
}

export interface CampaignDailyComparison {
  date_today: string;
  date_yesterday: string;
  spend_rub: MetricComparison;
  impressions: MetricComparison;
  clicks: MetricComparison;
  sales_promo_rub: MetricComparison;
}

export interface CampaignAutoDailyComparison {
  date_today: string;
  date_yesterday: string;
  spend_rub: MetricComparison;
  impressions: MetricComparison;
  clicks: MetricComparison;
  revenue_rub: MetricComparison;
}

// Same shape/purpose as CampaignDetail below, but aggregated from the
// automatically-collected AdvertisingDailyStatistic (Ozon Performance API)
// instead of the CSV-uploaded AdvertisingStatistic — kept separate, never
// merged with the fields above it, to avoid double-counting spend/revenue.
export interface CampaignAutoDailyDetail {
  has_data: boolean;
  total_spend_rub: number;
  total_revenue_rub: number;
  total_impressions: number;
  total_clicks: number;
  total_orders: number;
  drr_calculated_pct: number | null;
  roas_calculated: number | null;
  period_start: string | null;
  period_end: string | null;
  daily_comparison: CampaignAutoDailyComparison | null;
  daily_comparison_unavailable_reason: string | null;
}

// AI-generated advertising-campaign review (see backend
// app.services.advertising_ai_review_service) — one per (store, period),
// generated automatically once a day or on demand. Deliberately has no
// ДРР/ROAS/orders field — the input data doesn't include them yet.
export interface AdvertisingAiReviewInsight {
  ozon_campaign_id: string;
  campaign_name: string;
  assessment: "strong" | "weak" | "neutral";
  note: string;
}

export interface AdvertisingAiReview {
  id: string;
  period_start: string;
  period_end: string;
  campaigns_analyzed: number;
  overview: string;
  insights: AdvertisingAiReviewInsight[];
  anomalies: string[];
  recommendations: string[];
  model_used: string | null;
  created_at: string;
}

export interface AdvertisingAiReviewListResponse {
  items: AdvertisingAiReview[];
}

export interface CampaignDetail {
  campaign_id: string;
  has_data: boolean;
  total_spend_rub: number;
  total_sales_promo_rub: number;
  total_impressions: number;
  total_clicks: number;
  total_units_sold: number;
  drr_calculated_pct: number | null;
  roas_calculated: number | null;
  period_start: string | null;
  period_end: string | null;
  daily_comparison: CampaignDailyComparison | null;
  daily_comparison_unavailable_reason: string | null;
  auto_daily: CampaignAutoDailyDetail;
}

export interface ProductCardStatistic {
  id: string;
  product_id: string | null;
  product_name: string | null;
  ozon_sku: string;
  offer_id: string | null;
  brand: string | null;
  category_l1: string | null;
  fulfillment_scheme: string | null;
  date: string;
  impressions_total: number | null;
  impressions_search_catalog: number | null;
  card_visits: number | null;
  cart_adds_total: number | null;
  ordered_units: number | null;
  delivered_units: number | null;
  bought_out_units: number | null;
  cancelled_units_by_order_date: number | null;
  returned_units_by_order_date: number | null;
  ordered_sum_actual_price_rub: number | null;
  search_catalog_position_ozon: number | null;
  conv_impression_to_order_pct_ozon: number | null;
  conv_to_cart_total_pct_ozon: number | null;
  conv_cart_to_order_pct_ozon: number | null;
  conv_order_to_buyout_pct_ozon: number | null;
  avg_price_rub: number | null;
  price_index_label_ozon: string | null;
  drr_pct_ozon: number | null;
  stock_end_of_period: number | null;
  reviews_count: number | null;
  rating: number | null;
}

export interface ProductCardStatisticListResponse {
  items: ProductCardStatistic[];
  total: number;
}

export interface ProductCardAnalytics {
  has_data: boolean;
  date_from: string | null;
  date_to: string | null;
  total_impressions: number;
  total_card_visits: number;
  total_cart_adds: number;
  total_ordered_units: number;
  total_delivered_units: number;
  total_bought_out_units: number;
  total_cancelled_units: number;
  total_returned_units: number;
  total_ordered_sum_rub: number;
  latest_stock: number | null;
  latest_reviews_count: number | null;
  latest_rating: number | null;
  latest_avg_price_rub: number | null;
  latest_price_index_label: string | null;
  cart_conversion_calculated_pct: number | null;
  order_conversion_calculated_pct: number | null;
  buyout_rate_calculated_pct: number | null;
  rating_trend: { date: string; rating: number }[];
  stock_trend: { date: string; stock: number }[];
}

export interface SearchQueryStatistic {
  id: string;
  product_id: string | null;
  product_name: string | null;
  ozon_sku: string;
  offer_id: string | null;
  query_text: string;
  period_start: string;
  period_end: string;
  people_searched: number | null;
  people_saw: number | null;
  position_ozon: number | null;
  conv_search_to_card_pct_ozon: number | null;
  conv_search_to_order_pct_ozon: number | null;
  ordered_units_by_query: number | null;
  ordered_sum_by_query_rub: number | null;
  // API-only (null for rows from the manual XLSX/CSV upload): Ozon's own
  // ranking of this query's importance/traffic for the SKU — NOT the
  // search-results position (see position_ozon for that).
  query_index: number | null;
}

export interface SearchQueryStatisticListResponse {
  items: SearchQueryStatistic[];
  total: number;
}

export interface TopQueryItem {
  query_text: string;
  people_searched: number | null;
  people_saw: number | null;
  position_ozon: number | null;
  ordered_units_by_query: number | null;
  ordered_sum_by_query_rub: number | null;
}

export interface SearchQueryAnalytics {
  has_data: boolean;
  period_start: string | null;
  period_end: string | null;
  distinct_queries: number;
  total_people_searched: number;
  total_people_saw: number;
  total_ordered_units: number;
  total_ordered_sum_rub: number;
  order_rate_calculated_pct: number | null;
  avg_position_calculated: number | null;
  top_queries_by_searches: TopQueryItem[];
  top_queries_by_orders: TopQueryItem[];
}

// One (SKU, query) pair's most recent position vs. the snapshot before it —
// see backend app.schemas.search_query.SearchQueryPositionOut. Built from
// the same SearchQueryStatistic history as SearchQueryStatistic above; no
// separate "positions" data source.
export interface SearchQueryPosition {
  ozon_sku: string;
  offer_id: string | null;
  product_id: string | null;
  product_name: string | null;
  query_text: string;
  report_date: string;
  position: number | null;
  previous_report_date: string | null;
  previous_position: number | null;
  position_change: number | null;
  position_change_direction: "up" | "down" | null;
  people_searched: number | null;
  people_saw: number | null;
  ordered_units_by_query: number | null;
  query_index: number | null;
}

export interface SearchQueryPositionListResponse {
  items: SearchQueryPosition[];
  total: number;
}

// "Результаты по запросу" — a stateless preview of one query's full search
// result page with competitors (see backend
// app.schemas.search_query.QueryCompetitorsReportOut). Never persisted;
// deliberately separate from SearchQueryPosition's history/highlighting.
export interface QueryCompetitorRow {
  position: number;
  ozon_product_id: number | null;
  product_name: string | null;
  seller_name: string | null;
  overall_score: number | null;
  status: string | null;
  cpc_bid_rub: number | null;
  strategy: string | null;
  cpo_bid_text: string | null;
  relevance_pct: number | null;
  reviews_text: string | null;
  price_rub: number | null;
  popularity_score: number | null;
  ozon_promotions: string | null;
  delivery_term: string | null;
  price_index_pct: number | null;
}

export interface QueryCompetitorsReport {
  query_text: string;
  region: string | null;
  generated_at: string | null;
  positions_in_results: number | null;
  rows: QueryCompetitorRow[];
}

// Store-wide daily dashboard (GET /stores/{id}/dashboard) — see backend
// app.schemas.dashboard's module docstring for why each block is
// independently has_data and what spend_share_of_revenue_pct means.
export interface DashboardMetric {
  current: number;
  previous: number | null;
  delta_pct: number | null;
  direction: "up" | "down" | null;
}

export interface OrdersRevenueBlock {
  has_data: boolean;
  orders: DashboardMetric | null;
  revenue_rub: DashboardMetric | null;
  avg_order_value_rub: number | null;
}

export interface AdvertisingDashboardBlock {
  has_data: boolean;
  spend_auto_rub: DashboardMetric | null;
  spend_manual_rub: DashboardMetric | null;
  spend_share_of_revenue_pct: number | null;
}

export interface ReviewsDashboardBlock {
  has_data: boolean;
  new_count: DashboardMetric | null;
  avg_rating_current: number | null;
  avg_rating_previous: number | null;
  without_reply_count: number | null;
}

export interface MarginBlock {
  has_data: boolean;
  delivered_units: number | null;
  delivered_sum_rub: number | null;
  commission_rub: number | null;
  cost_of_delivered_rub: number | null;
  cost_known: boolean | null;
  margin_rub: number | null;
  margin_pct: number | null;
}

export interface Dashboard {
  period_start: string;
  period_end: string;
  previous_period_start: string;
  previous_period_end: string;
  orders_revenue: OrdersRevenueBlock;
  advertising: AdvertisingDashboardBlock;
  reviews: ReviewsDashboardBlock;
  margin: MarginBlock;
}

// "РНП" — daily order/revenue/buyout/cancellation statistics from Ozon
// Seller API postings (FBO/FBS), see backend
// app.models.order_daily_statistic's module docstring for the confirmed
// contract and what it deliberately does NOT cover yet (search-in-card
// funnel, logistics/storage/penalties breakdown).
export interface OrderDailyStatistic {
  id: string;
  date: string;
  delivery_schema: "FBO" | "FBS";
  ordered_units: number;
  ordered_sum_rub: number;
  ordered_sum_discounted_rub: number;
  delivered_units: number;
  delivered_sum_rub: number;
  cost_of_delivered_rub: number;
  cost_of_delivered_known_units: number;
  cancelled_units: number;
  cancelled_sum_rub: number;
  unfinished_units: number;
  commission_rub: number;
}

export interface OrderDailyStatisticListResponse {
  items: OrderDailyStatistic[];
  total: number;
}
