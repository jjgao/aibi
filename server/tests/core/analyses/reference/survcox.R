# Reference outputs for survival.cox's model of cohorts and covariates (SPEC §9.5; D362–D365).
#
# Run with the survival package, from this directory:  Rscript survcox.R > survcox.json
# The checked-in survcox.json was written by R 4.3.3 with survival 3.5-8.
#
# Each case is a design: per unit its position (0 the reference), an entry (or none), a time, a
# status, and its covariates, NA where the unit has no value (a complete-case analysis leaves it
# out, as coxph's na.omit does); `kinds` names each covariate's coding (number, boolean,
# category) and `stratum` whether the last covariate is the stratum. The cohort terms are 0/1
# columns of each other position; a boolean is 0/1; a category is a factor whose first level is
# the baseline by D363's rule (the most common level among those with an event among the
# complete cases, ties by the smallest text; the names here are lowercase ASCII, whose order in
# R's sort is that of their canonical text as UTF-16 code units), its other levels in that
# order. coxph's fit (ties = "efron", timefix = FALSE): per term the hazard ratio, the Wald
# interval at 0.95 and the p-value of summary(), per category of two or more levels the joint
# Wald test (b' V^-1 b) and its p-value, and cox.zph's global test (transform = "km"); n and
# nevent.

suppressPackageStartupMessages(library(survival))

num <- function(x) if (length(x) == 0 || is.na(x) || !is.finite(x)) "null" else sprintf("%.17g", x)
vec <- function(x) paste0("[", paste(vapply(x, num, ""), collapse = ", "), "]")
str <- function(x) paste0("[", paste(ifelse(is.na(x), "null", sprintf("\"%s\"", x)), collapse = ", "), "]")

baseline <- function(values, status) {
  keep <- !is.na(values)
  levels <- sort(unique(values[keep]))
  evented <- levels[levels %in% unique(values[keep & status == 1])]
  if (length(evented) == 0) evented <- levels
  counts <- sapply(evented, function(l) sum(values[keep] == l))
  evented[order(-counts, evented)][1]
}

cases <- list()
add <- function(name, d, kinds, stratum = FALSE) {
  cases[[length(cases) + 1]] <<- list(name = name, d = d, kinds = kinds, stratum = stratum)
}

set.seed(20260928)
make <- function(n, positions, entries) {
  g <- sample(0:(positions - 1), n, replace = TRUE)
  x <- round(rnorm(n), 3)
  b <- rbinom(n, 1, .4)
  c <- sample(c("a", "b", "c"), n, replace = TRUE, prob = c(.5, .3, .2))
  lp <- 0.4 * (g == 1) - 0.3 * (g == 2) + 0.5 * x + 0.6 * b + 0.4 * (c == "b")
  t <- round(rexp(n, exp(lp)) * 10, 2) + 0.01
  e <- if (entries) round(runif(n, 0, 0.5) * t, 2) else NULL
  list(g = g, x = x, b = b, c = c, t = t, s = rbinom(n, 1, .75), e = e)
}
d <- make(160, 2, FALSE)
d$x[c(3, 17, 40)] <- NA; d$c[c(5, 60)] <- NA; d$b[c(9)] <- NA
add("two cohorts, a number, a boolean and a category, with missing values",
    list(g = d$g, e = NULL, t = d$t, s = d$s, x = d$x, b = d$b, c = d$c),
    c("number", "boolean", "category"))
d <- make(220, 3, TRUE)
st <- sample(c("p", "q", "r"), 220, replace = TRUE)
add("three cohorts, a category and a stratum, with entries",
    list(g = d$g, e = d$e, t = d$t, s = d$s, c = d$c, st = st), c("category", "category"), TRUE)
d <- make(120, 1, FALSE)
add("one cohort, a number and a category", list(g = d$g, e = NULL, t = d$t, s = d$s, x = d$x, c = d$c),
    c("number", "category"))
d <- make(150, 3, FALSE)
st <- sample(c("u", "v"), 150, replace = TRUE)
add("three cohorts stratified", list(g = d$g, e = NULL, t = d$t, s = d$s, st = st), c("category"), TRUE)
d <- make(140, 2, TRUE)
st <- sample(1:3, 140, replace = TRUE)
add("a number stratified, with entries", list(g = d$g, e = d$e, t = d$t, s = d$s, x = d$x, st = st),
    c("number", "category"), TRUE)
d <- make(180, 2, FALSE)
add("a category whose counts tie", list(g = d$g, e = NULL, t = d$t, s = d$s, c = rep(c("m", "n", "o"), 60)),
    c("category"))

entries <- character()
for (case in cases) {
  d <- case$d
  covs <- setdiff(names(d), c("g", "e", "t", "s"))
  frame <- data.frame(t = d$t, s = d$s)
  if (!is.null(d$e)) frame$e <- d$e
  positions <- sort(unique(d$g))
  terms <- character()
  for (p in positions[positions != 0]) { frame[[sprintf("g%d", p)]] <- as.numeric(d$g == p); terms <- c(terms, sprintf("g%d", p)) }
  complete <- rep(TRUE, length(d$t))
  for (v in covs) complete <- complete & !is.na(d[[v]])
  kinds <- case$kinds
  tested <- character()
  for (j in seq_along(covs)) {
    v <- covs[j]
    if (case$stratum && j == length(covs)) { frame$st <- d[[v]]; next }
    if (kinds[j] == "category") {
      base <- baseline(ifelse(complete, d[[v]], NA), d$s)
      others <- setdiff(sort(unique(d[[v]][complete & !is.na(d[[v]])])), base)
      frame[[v]] <- factor(d[[v]], levels = c(base, others))
      terms <- c(terms, v)
      if (length(others) >= 2) tested <- c(tested, v)
    } else {
      frame[[v]] <- d[[v]]
      terms <- c(terms, v)
    }
  }
  surv <- if (is.null(d$e)) "Surv(t, s)" else "Surv(e, t, s)"
  formula <- as.formula(paste(surv, "~", paste(terms, collapse = " + "), if (case$stratum) "+ strata(st)" else ""))
  fit <- coxph(formula, data = frame, ties = "efron", control = coxph.control(timefix = FALSE))
  s <- summary(fit, conf.int = 0.95)
  b <- coef(fit); V <- vcov(fit)
  wald <- character()
  for (v in tested) {
    k <- grep(paste0("^", v), names(b))
    stat <- as.numeric(t(b[k]) %*% solve(V[k, k]) %*% b[k])
    wald <- c(wald, sprintf("{\"statistic\": %s, \"df\": %d, \"p\": %s}", num(stat), length(k),
                            num(pchisq(stat, length(k), lower.tail = FALSE))))
  }
  zph <- tryCatch(cox.zph(fit, transform = "km")$table["GLOBAL", ], error = function(e) NULL)
  cov_json <- paste(sapply(covs, function(v) {
    x <- d[[v]]
    if (is.character(x)) str(x) else vec(x)
  }), collapse = ", ")
  entries <- c(entries, sprintf(paste0(
    "{\"name\": \"%s\", \"position\": %s, \"entry\": %s, \"time\": %s, \"status\": %s, ",
    "\"covariates\": [%s], \"kinds\": %s, \"stratum\": %s, \"hr\": %s, \"low\": %s, \"high\": %s, ",
    "\"p\": %s, \"wald\": [%s], \"zph\": %s, \"n\": %d, \"nevent\": %d}"),
    case$name, vec(d$g), if (is.null(d$e)) "null" else vec(d$e), vec(d$t), vec(d$s), cov_json,
    str(kinds), if (case$stratum) "true" else "false",
    vec(s$conf.int[, 1]), vec(s$conf.int[, 3]), vec(s$conf.int[, 4]), vec(s$coefficients[, 5]),
    paste(wald, collapse = ", "),
    if (is.null(zph)) "null" else sprintf("{\"statistic\": %s, \"df\": %s, \"p\": %s}", num(zph[1]), num(zph[2]), num(zph[3])),
    fit$n, fit$nevent))
}
cat(sprintf("{\"r\": \"%s\", \"survival\": \"%s\",\n\"cases\": [%s]}\n", R.version.string,
            as.character(packageVersion("survival")), paste(entries, collapse = ",\n  ")))
