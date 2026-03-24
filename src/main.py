"""
GitHub Intelligence Platform
Extracts repo signals for 2 dashboards (BigQuery + Power BI)

Dashboard 1: Technology Trends & Market Analysis
Dashboard 2: Health & Productivity Dashboard
"""

import os
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery
from google.api_core.exceptions import NotFound

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# BIG 5 TECH COMPANIES & THEIR KEY REPOSITORIES
# ============================================================================

BIG_5_COMPANIES: Dict[str, Dict[str, Any]] = {
    "Meta": {
        "company_id": 1,
        "category": "Social Media & AI",
        "repositories": [
            ("facebook", "react"),
            ("pytorch", "pytorch"),
            ("facebook", "flow"),
            ("facebookresearch", "detectron2"),
            ("facebookresearch", "fairseq"),
            ("facebook", "docusaurus"),
            ("facebook", "prophet"),
        ],
    },
    "Google": {
        "company_id": 2,
        "category": "Cloud & ML",
        "repositories": [
            ("tensorflow", "tensorflow"),
            ("golang", "go"),
            ("google", "protobuf"),
            ("google", "googletest"),
            ("google", "flatbuffers"),
        ],
    },
    "Microsoft": {
        "company_id": 3,
        "category": "Cloud & Development",
        "repositories": [
            ("microsoft", "vscode"),
            ("microsoft", "TypeScript"),
            ("dotnet", "runtime"),
            ("microsoft", "terminal"),
            ("microsoft", "playwright"),
            ("microsoft", "WSL"),
            ("microsoft", "PowerToys"),
        ],
    },
    "Amazon": {
        "company_id": 4,
        "category": "Cloud & E-commerce",
        "repositories": [
            ("aws", "aws-cli"),
            ("aws", "aws-cdk"),
            ("aws-amplify", "amplify-js"),
            ("awsdocs", "aws-doc-sdk-examples"),
        ],
    },
    "Apple": {
        "company_id": 5,
        "category": "Hardware & Software",
        "repositories": [
            ("apple", "swift"),
            ("apple", "coremltools"),
            ("apple", "foundationdb"),
        ],
    },
}

# ============================================================================
# MAPPINGS / CLASSIFICATION
# ============================================================================

TECH_CATEGORY_MAP: Dict[str, str] = {
    "Python": "Data Science & ML",
    "JavaScript": "Web & Frontend",
    "TypeScript": "Web & Frontend",
    "Go": "Infrastructure & Systems",
    "Rust": "Infrastructure & Systems",
    "C": "Systems & Core",
    "C++": "Systems & Core",
    "Java": "Enterprise & Backend",
    "Swift": "Mobile & iOS",
    "Kotlin": "Mobile & Android",
    "Shell": "DevOps & Tools",
    "Makefile": "DevOps & Tools",
}

REPO_TYPE_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(react|vue|angular|django|flask)\b", "Framework"),
    (r"\b(library|sdk|client)\b", "Library"),
    (r"\b(cli|command[- ]line|tool)\b", "Tool"),
    (r"\b(doc|docs|documentation)\b", "Documentation"),
    (r"\b(example|examples|sample|samples|template|starter)\b", "Example"),
]


# ============================================================================
# HELPERS
# ============================================================================

def safe_div(n: float, d: float, default: float = 0.0) -> float:
    return default if d == 0 else n / d


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def get_tech_category(language: Optional[str]) -> str:
    if not language:
        return "Other"
    return TECH_CATEGORY_MAP.get(language, "Other")


def compute_repo_maturity(age_in_years: float) -> str:
    if age_in_years < 1:
        return "New"
    if age_in_years < 3:
        return "Growing"
    if age_in_years < 5:
        return "Mature"
    return "Established"


def compute_activity_status(days_since_last_push: Optional[int]) -> str:
    if days_since_last_push is None:
        return "Unknown"
    if days_since_last_push == 0:
        return "Very Active (Today)"
    if days_since_last_push <= 7:
        return "Active (This Week)"
    if days_since_last_push <= 30:
        return "Recently Updated"
    if days_since_last_push <= 90:
        return "Somewhat Inactive"
    return "Dormant/Abandoned"


def compute_repo_type(repo_name: str, description: Optional[str]) -> str:
    hay = f"{repo_name} {description or ''}".lower()
    for pattern, label in REPO_TYPE_PATTERNS:
        if re.search(pattern, hay):
            return label
    return "Other"


def compute_health_score_0_100(
    stars: int,
    forks: int,
    open_issues: int,
    days_since_last_push: Optional[int],
    max_stars: int,
    max_forks: int,
) -> float:
    popularity = safe_div(stars, max_stars, default=0.0) * 40.0
    adoption = safe_div(min(forks, max_forks), max_forks, default=0.0) * 20.0
    issue_mgmt = clamp(100.0 - (open_issues / 100.0), 0.0, 100.0) * 0.20
    activity = safe_div(min(days_since_last_push or 0, 30), 30.0, default=0.0) * 20.0
    return clamp(popularity + adoption + issue_mgmt + activity, 0.0, 100.0)


def compute_growth_potential_0_100(
    forks: int,
    stars: int,
    age_in_days: int,
    days_since_last_push: Optional[int],
    open_issues: int,
) -> float:
    community_interest = safe_div(forks, max(stars, 1), default=0.0) * 30.0
    growth_rate = safe_div(stars, max(age_in_days, 1), default=0.0) * 20.0
    recent_activity = safe_div(min(days_since_last_push or 0, 7), 7.0, default=0.0) * 25.0
    engagement = safe_div(min(open_issues, 50), 50.0, default=0.0) * 25.0
    return clamp(community_interest + growth_rate + recent_activity + engagement, 0.0, 100.0)


def compute_maintenance_burden_0_100(open_issues: int, stars: int, days_since_last_push: Optional[int], forks: int) -> float:
    a = safe_div(open_issues, (stars + 1), default=0.0) * 40.0
    b = safe_div(max(30 - (days_since_last_push or 0), 0), 30.0, default=0.0) * 40.0
    c = safe_div(forks, max(stars, 1), default=0.0) * 20.0
    return clamp(a + b + c, 0.0, 100.0)


def compute_quality_score_0_100(open_issues: int, stars: int, days_since_last_push: Optional[int], maintenance_burden: float) -> float:
    issue_ratio = safe_div(open_issues, max(stars, 1), default=0.0) * 50.0
    inactivity = safe_div(max(days_since_last_push or 0, 0), 180.0, default=0.0) * 30.0
    maint = safe_div(min(maintenance_burden, 100.0), 100.0, default=0.0) * 20.0
    return clamp(100.0 - (issue_ratio + inactivity + maint), 0.0, 100.0)


# ============================================================================
# GITHUB EXTRACTOR
# ============================================================================

class EnhancedGitHubExtractor:
    def __init__(self, token: Optional[str] = None) -> None:
        if token is None:
            token = os.getenv("GH_TOKEN")
        if not token:
            raise ValueError("GH_TOKEN environment variable not set")

        from github import Github, Auth

        auth = Auth.Token(token)
        self.github = Github(auth=auth, per_page=100)
        logger.info("✅ GitHub client initialized")

    def _topics(self, repo) -> List[str]:
        try:
            return [str(t) for t in (repo.get_topics() or [])]
        except Exception:
            return []

    def _count_releases(self, repo) -> Optional[int]:
        try:
            rels = repo.get_releases()
            return int(rels.totalCount) if hasattr(rels, "totalCount") else None
        except Exception:
            return None

    def _commits_last_n_days(self, repo, days: int = 30) -> Optional[int]:
        try:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            commits = repo.get_commits(since=since)
            return int(commits.totalCount) if hasattr(commits, "totalCount") else None
        except Exception:
            return None

    def _contributors_count(self, repo) -> Optional[int]:
        try:
            contribs = repo.get_contributors(anon="true")
            return int(contribs.totalCount) if hasattr(contribs, "totalCount") else None
        except Exception:
            return None

    def _prs_counts_last_30_days(self, repo) -> Dict[str, Optional[int]]:
        out = {"open_prs": None, "closed_prs_last_30_days": None}

        try:
            pulls_open = repo.get_pulls(state="open", sort="updated", direction="desc")
            out["open_prs"] = int(pulls_open.totalCount) if hasattr(pulls_open, "totalCount") else None
        except Exception:
            pass

        try:
            since = datetime.now(timezone.utc) - timedelta(days=30)
            pulls_closed = repo.get_pulls(state="closed", sort="updated", direction="desc")
            c = 0
            for pr in pulls_closed[:300]:
                closed_at = getattr(pr, "closed_at", None)
                if closed_at and closed_at >= since:
                    c += 1
            out["closed_prs_last_30_days"] = c
        except Exception:
            pass

        return out

    def extract_repo_row(
        self,
        owner: str,
        repo: str,
        company_name: str,
        company_id: int,
        company_category: str,
    ) -> Optional[Dict[str, Any]]:
        full_name = f"{owner}/{repo}"
        try:
            r = self.github.get_repo(full_name)

            now_utc = datetime.now(timezone.utc)
            pushed_at = r.pushed_at or r.updated_at
            created_at = r.created_at

            days_since_last_push = (now_utc - pushed_at).days if pushed_at else None
            age_in_days = (now_utc - created_at).days if created_at else 0
            age_in_years = safe_div(float(age_in_days), 365.0, default=0.0)

            topics = self._topics(r)
            releases_count = self._count_releases(r)
            commits_last_30_days = self._commits_last_n_days(r, days=30)
            contributors_count = self._contributors_count(r)
            pr_counts = self._prs_counts_last_30_days(r)

            stars = int(r.stargazers_count or 0)
            forks = int(r.forks_count or 0)
            open_issues = int(r.open_issues_count or 0)

            language = str(r.language) if r.language else None
            tech_category = get_tech_category(language)
            repository_type = compute_repo_type(str(r.name), r.description)

            repo_maturity = compute_repo_maturity(age_in_years)
            activity_status = compute_activity_status(days_since_last_push)

            popularity_per_year = safe_div(float(stars), max(age_in_years, 1e-9), default=0.0)
            fork_engagement_rate = safe_div(float(forks), float(max(stars, 1)), default=0.0) * 100.0
            issue_density = safe_div(float(open_issues), float(max(1, stars)), default=0.0)

            growth_potential = compute_growth_potential_0_100(
                forks=forks,
                stars=stars,
                age_in_days=max(age_in_days, 1),
                days_since_last_push=days_since_last_push,
                open_issues=open_issues,
            )

            maintenance_burden = compute_maintenance_burden_0_100(
                open_issues=open_issues,
                stars=stars,
                days_since_last_push=days_since_last_push,
                forks=forks,
            )

            quality_score = compute_quality_score_0_100(
                open_issues=open_issues,
                stars=stars,
                days_since_last_push=days_since_last_push,
                maintenance_burden=maintenance_burden,
            )

            return {
                "repo_id": int(r.id),
                "repo_full_name": str(r.full_name),
                "repo_name": str(r.name),
                "repo_owner": str(r.owner.login),
                "company_name": company_name,
                "company_id": int(company_id),
                "category": company_category,
                "stars": stars,
                "forks": forks,
                "open_issues": open_issues,
                "created_at": created_at.isoformat() if created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "pushed_at": pushed_at.isoformat() if pushed_at else None,
                "days_since_last_push": int(days_since_last_push) if days_since_last_push is not None else None,
                "extracted_at": now_utc.isoformat(),

                "language": language,
                "topics": ",".join(topics) if topics else None,
                "releases_count": int(releases_count) if releases_count is not None else None,
                "commits_last_30_days": int(commits_last_30_days) if commits_last_30_days is not None else None,
                "contributors_count": int(contributors_count) if contributors_count is not None else None,
                "open_prs": int(pr_counts["open_prs"]) if pr_counts["open_prs"] is not None else None,
                "closed_prs_last_30_days": int(pr_counts["closed_prs_last_30_days"]) if pr_counts["closed_prs_last_30_days"] is not None else None,
                "age_in_days": int(age_in_days),
                "age_in_years": float(age_in_years),

                "tech_category": tech_category,
                "health_score": None,
                "repo_maturity": repo_maturity,
                "activity_status": activity_status,
                "growth_potential": float(growth_potential),
                "popularity_per_year": float(popularity_per_year),
                "fork_engagement_rate": float(fork_engagement_rate),
                "issue_density": float(issue_density),
                "maintenance_burden": float(maintenance_burden),
                "quality_score": float(quality_score),
                "company_tech_diversity": None,
                "company_repo_rank": None,
                "language_market_share": None,
                "repository_type": repository_type,
                "industry_relevance": None,
            }

        except Exception as e:
            logger.error(f"❌ Error extracting {full_name}: {e}")
            return None


# ============================================================================
# BIGQUERY LOADER (create table if missing)
# ============================================================================

def coerce_df_to_schema(df: pd.DataFrame, schema: List[bigquery.SchemaField]) -> pd.DataFrame:
    wanted = [f.name for f in schema]

    for c in wanted:
        if c not in df.columns:
            df[c] = pd.NA

    extras = [c for c in df.columns if c not in wanted]
    if extras:
        logger.warning(f"Dropping unexpected columns before load: {extras}")
        df = df.drop(columns=extras)

    return df[wanted]


class BigQueryLoader:
    def __init__(self, dataset_id: str = "github_insights") -> None:
        gcp_project_id = os.getenv("GCP_PROJECT_ID")
        gcp_sa_key = os.getenv("GCP_SA_KEY")

        if not gcp_project_id:
            raise ValueError("GCP_PROJECT_ID not set")
        if not gcp_sa_key:
            raise ValueError("GCP_SA_KEY not set (path to service account JSON)")

        self.project_id = gcp_project_id
        self.dataset_id = dataset_id
        self.client = bigquery.Client.from_service_account_json(gcp_sa_key, project=self.project_id)
        logger.info("✅ BigQuery client initialized")

    def _dataset_ref(self) -> str:
        return f"{self.project_id}.{self.dataset_id}"

    def _table_ref(self, table_name: str) -> str:
        return f"{self.project_id}.{self.dataset_id}.{table_name}"

    def ensure_dataset(self) -> None:
        dataset_id = self._dataset_ref()
        try:
            self.client.get_dataset(dataset_id)
        except NotFound:
            dataset = bigquery.Dataset(dataset_id)
            dataset.location = "US"
            self.client.create_dataset(dataset)

    def create_table_if_missing(self, table_name: str, schema: List[bigquery.SchemaField]) -> None:
        self.ensure_dataset()
        table_id = self._table_ref(table_name)
        try:
            self.client.get_table(table_id)
            logger.info(f"Table {table_id} already exists.")
        except NotFound:
            table = bigquery.Table(table_id, schema=schema)
            self.client.create_table(table)
            logger.info(f"Table {table_id} created.")

    def load_append(self, table_name: str, df: pd.DataFrame, schema: List[bigquery.SchemaField]) -> None:
        if df.empty:
            logger.warning("No rows to load; skipping BigQuery load.")
            return

        table_id = self._table_ref(table_name)
        df = coerce_df_to_schema(df, schema)

        for col in ["created_at", "updated_at", "pushed_at", "extracted_at"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            schema=schema,
        )
        job = self.client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        logger.info(f"✅ Loaded {len(df)} rows into {table_id} (APPEND).")


# ============================================================================
# POST-PROCESS CALCULATIONS
# ============================================================================

def add_company_and_global_calcs(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    max_stars = int(df["stars"].max()) if df["stars"].notna().any() else 1
    max_forks = int(df["forks"].max()) if df["forks"].notna().any() else 1
    avg_stars_global = float(df["stars"].mean()) if df["stars"].notna().any() else 0.0

    df["health_score"] = df.apply(
        lambda r: float(
            compute_health_score_0_100(
                stars=int(r["stars"]),
                forks=int(r["forks"]),
                open_issues=int(r["open_issues"]),
                days_since_last_push=None if pd.isna(r["days_since_last_push"]) else int(r["days_since_last_push"]),
                max_stars=max_stars,
                max_forks=max_forks,
            )
        ),
        axis=1,
    )

    distinct_lang = df.groupby("company_name")["language"].nunique(dropna=True)
    total_repos = df.groupby("company_name")["repo_full_name"].count()
    diversity = (distinct_lang / total_repos).fillna(0.0)
    df["company_tech_diversity"] = df["company_name"].map(diversity).astype(float)

    df["company_repo_rank"] = df.groupby("company_name")["stars"].rank(method="dense", ascending=False).astype("Int64")

    company_total_stars = df.groupby("company_name")["stars"].transform("sum")
    lang_stars = df.groupby(["company_name", "language"])["stars"].transform("sum")
    df["language_market_share"] = ((lang_stars / company_total_stars.replace({0: pd.NA})) * 100.0).fillna(0.0)

    def _industry_rel(row) -> int:
        days = None if pd.isna(row["days_since_last_push"]) else int(row["days_since_last_push"])
        stars = int(row["stars"])
        forks = int(row["forks"])
        open_issues = int(row["open_issues"])

        score = 0
        score += 30 if (days is not None and days <= 7) else 0
        score += 20 if open_issues > 0 else 0
        score += 30 if forks > (stars * 0.1) else 0
        score += 20 if stars > avg_stars_global else 0
        return int(score)

    df["industry_relevance"] = df.apply(_industry_rel, axis=1).astype("Int64")

    return df


# ============================================================================
# MAIN
# ============================================================================

def main() -> None:
    logger.info("🔧 Initializing extractors and loaders...")
    extractor = EnhancedGitHubExtractor()
    bq_loader = BigQueryLoader(dataset_id="github_insights")

    table_name = "big5_repositories"

    schema: List[bigquery.SchemaField] = [
        bigquery.SchemaField("repo_id", "INT64"),
        bigquery.SchemaField("repo_full_name", "STRING"),
        bigquery.SchemaField("repo_name", "STRING"),
        bigquery.SchemaField("repo_owner", "STRING"),
        bigquery.SchemaField("company_name", "STRING"),
        bigquery.SchemaField("company_id", "INT64"),
        bigquery.SchemaField("category", "STRING"),
        bigquery.SchemaField("stars", "INT64"),
        bigquery.SchemaField("forks", "INT64"),
        bigquery.SchemaField("open_issues", "INT64"),
        bigquery.SchemaField("created_at", "TIMESTAMP"),
        bigquery.SchemaField("updated_at", "TIMESTAMP"),
        bigquery.SchemaField("pushed_at", "TIMESTAMP"),
        bigquery.SchemaField("days_since_last_push", "INT64"),
        bigquery.SchemaField("extracted_at", "TIMESTAMP"),

        bigquery.SchemaField("language", "STRING"),
        bigquery.SchemaField("topics", "STRING"),
        bigquery.SchemaField("releases_count", "INT64"),
        bigquery.SchemaField("commits_last_30_days", "INT64"),
        bigquery.SchemaField("contributors_count", "INT64"),
        bigquery.SchemaField("open_prs", "INT64"),
        bigquery.SchemaField("closed_prs_last_30_days", "INT64"),
        bigquery.SchemaField("age_in_days", "INT64"),
        bigquery.SchemaField("age_in_years", "FLOAT64"),

        bigquery.SchemaField("tech_category", "STRING"),
        bigquery.SchemaField("health_score", "FLOAT64"),
        bigquery.SchemaField("repo_maturity", "STRING"),
        bigquery.SchemaField("activity_status", "STRING"),
        bigquery.SchemaField("growth_potential", "FLOAT64"),
        bigquery.SchemaField("popularity_per_year", "FLOAT64"),
        bigquery.SchemaField("fork_engagement_rate", "FLOAT64"),
        bigquery.SchemaField("issue_density", "FLOAT64"),
        bigquery.SchemaField("maintenance_burden", "FLOAT64"),
        bigquery.SchemaField("quality_score", "FLOAT64"),
        bigquery.SchemaField("company_tech_diversity", "FLOAT64"),
        bigquery.SchemaField("company_repo_rank", "INT64"),
        bigquery.SchemaField("language_market_share", "FLOAT64"),
        bigquery.SchemaField("repository_type", "STRING"),
        bigquery.SchemaField("industry_relevance", "INT64"),
    ]

    bq_loader.create_table_if_missing(table_name, schema)

    rows: List[Dict[str, Any]] = []
    for company_name, company_info in BIG_5_COMPANIES.items():
        company_id = int(company_info["company_id"])
        company_category = str(company_info["category"])
        for owner, repo in company_info["repositories"]:
            row = extractor.extract_repo_row(owner, repo, company_name, company_id, company_category)
            if row:
                rows.append(row)

    if not rows:
        logger.warning("⚠️ No repository data extracted. Nothing to load.")
        return

    df = pd.DataFrame(rows)
    df = add_company_and_global_calcs(df)

    logger.info(f"Extracted {len(df)} repository rows (dashboard-ready).")
    bq_loader.load_append(table_name, df, schema)
    logger.info("✅ Pipeline executed successfully!")


if __name__ == "__main__":
    main()