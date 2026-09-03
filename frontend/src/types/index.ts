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
  offer_id: string | null;
  name: string;
  image_url: string | null;
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
  reviews_fetched: number;
  reviews_created: number;
  reviews_skipped_duplicate: number;
  error_message: string | null;
}
