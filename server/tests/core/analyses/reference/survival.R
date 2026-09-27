# Reference outputs for the methods of survival.km (SPEC §9.5, §13.4; D349).
#
# Run with the survival package:  Rscript survival.R > survival.json
# The checked-in survival.json was written by R 4.3.3 with survival 3.5-8. Curves are
# survfit's (conf.type = "log-log", Greenwood's variance), their medians and intervals
# quantile.survfit's, landmarks summary.survfit's; a bound where the curve is 1 is written as 1
# (the curve's own bounds there are NA, summary's at time 0 are 1). At-risk counts at grid times
# count the units with entry < t <= time directly, as SPEC §9.5 defines them. The log-rank test is
# survdiff's, and over delayed entry the score test of coxph(ties = "exact"), its equal (§9.5);
# the hazard ratios are coxph's (ties = "efron") over the cohorts with events, the first the
# reference, with the infinite-coefficient flags of coxph.fit's rule (for a fit that converged);
# the test of proportional hazards is cox.zph's global test (transform = "km"), null where cox.zph
# fails on a singular information. Times are distinct beyond the tolerance of survival's timefix,
# or equal, so that it changes nothing. A fit that runs out of iterations is given entries at 0,
# which change no risk set, so that agreg.fit fits it: coxph.fit counts one iteration more than
# agreg.fit when it runs out, and the port counts as agreg.fit does.

suppressPackageStartupMessages(library(survival))

num <- function(x) if (length(x) == 0 || is.na(x) || !is.finite(x)) "null" else sprintf("%.17g", x)
vec <- function(x) paste0("[", paste(vapply(x, num, ""), collapse = ", "), "]")
obj <- function(...) {
  parts <- list(...)
  paste0("{", paste(sprintf("\"%s\": %s", names(parts), unlist(parts)), collapse = ", "), "}")
}
rows <- function(m) paste0("[", paste(apply(m, 1, vec), collapse = ", "), "]")
bound1 <- function(bound, surv) ifelse(surv == 1, 1, bound)

cases <- list()
case <- function(name, group, time, status, entry = NULL, stratum = NULL, level = 0.95,
                 landmarks = numeric(), grid = numeric(), iter_max = 20) {
  cases[[length(cases) + 1]] <<- list(name = name, group = group, time = time, status = status,
                                      entry = entry, stratum = stratum, level = level,
                                      landmarks = landmarks, grid = grid, iter_max = iter_max)
}

case("two groups with ties", c(1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2),
     c(1, 2, 2, 3, 4, 5, 1, 2, 3, 3, 6, 7, 8), c(0, 1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 1, 0),
     landmarks = c(0, 2, 2.5, 5, 9), grid = c(1, 3, 6, 9))
case("a curve at one half over an interval", c(1, 1, 1, 1, 2, 2, 2, 2, 2),
     c(1, 2, 5, 8, 1, 3, 4, 6, 9), c(1, 1, 0, 1, 0, 1, 1, 1, 0), landmarks = c(2, 4.5))
case("a curve that ends at one half", c(1, 1, 1, 1, 2, 2, 2), c(2, 3, 4, 6, 1, 2, 3),
     c(1, 0, 1, 0, 1, 1, 0))
case("a curve that reaches zero", c(1, 1, 1, 2, 2, 2, 2), c(1, 2, 3, 1, 2, 2, 5),
     c(1, 1, 1, 0, 1, 0, 1), level = 0.9, landmarks = c(1, 3, 4))
case("delayed entry", c(1, 1, 1, 1, 1, 2, 2, 2, 2, 2), c(1, 2, 2, 3, 4, 5, 6, 6, 7, 8),
     c(0, 1, 1, 0, 1, 0, 1, 1, 1, 1), entry = c(0, 0, 1, 1, 2, 0, 3, 0, 0, 1),
     landmarks = c(1.5, 6.5), grid = c(0.5, 2, 4, 8))
case("an event at time zero", c(1, 1, 1, 1, 2, 2, 2, 2), c(0, 0, 3, 5, 0, 2, 4, 6),
     c(1, 0, 1, 1, 0, 1, 0, 1), landmarks = c(0, 1))
case("a group whose events all come first", c(1, 1, 1, 1, 2, 2, 2, 2),
     c(1, 2, 3, 4, 10, 11, 12, 13), c(1, 1, 1, 1, 0, 1, 0, 1))
case("strata", c(1, 1, 1, 2, 2, 2, 1, 1, 2, 2, 2), c(2, 4, 6, 1, 3, 5, 3, 7, 1, 2, 8),
     c(1, 0, 1, 1, 1, 0, 1, 1, 1, 0, 1), stratum = c(1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2))
case("strata with delayed entry", c(1, 1, 1, 2, 2, 2, 1, 1, 2, 2, 2), c(2, 4, 6, 1, 3, 5, 3, 7, 1, 2, 8),
     c(1, 0, 1, 1, 1, 0, 1, 1, 1, 0, 1), entry = c(0, 1, 0, 0, 0, 2, 0, 2, 0, 0, 1),
     stratum = c(1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2))
halving <- list(group = c(rep(1, 11), rep(2, 5)),
                time = c(6, 13, 17, 15, 15, 19, 23, 11, 16, 9, 14, 1, 3, 6, 5, 2),
                status = c(1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1, 0))
case("a fit that halves its step twice", halving$group, halving$time, halving$status)
case("a fit that runs out of iterations after a worse step", halving$group, halving$time,
     halving$status, entry = rep(0, 16), iter_max = 2)
case("a fit that runs out of iterations after a better step", halving$group, halving$time,
     halving$status, entry = rep(0, 16), iter_max = 4)
case("a first step that overflows", c(rep(1, 2000), rep(2, 3)), c(1:2000, 1, 1, 1), rep(1, 2003))
case("a first step that underflows, 3 against 3000", c(rep(1, 3), rep(2, 3000)),
     c(1, 1, 1, 1, rep(2:31, each = 100)[1:2999]), rep(1, 3003))
case("a first step that underflows, 2 against 1500", c(rep(1, 2), rep(2, 1500)),
     c(1, 1, 1, rep(2:16, each = 100)[1:1499]), rep(1, 1502))
case("a first step that underflows, 1 against 1500", c(1, rep(2, 1500)),
     c(1, 1, rep(2:16, each = 100)[1:1499]), rep(1, 1501))

set.seed(20260927)
for (index in 1:4) {
  n <- c(40, 60, 90, 120)[index]
  groups <- if (index %% 2) 2 else 3
  g <- sample(seq_len(groups), n, replace = TRUE)
  t <- round(rexp(n, rate = 0.1 * (1 + (g - 1) / 2)), 1)
  s <- rbinom(n, 1, 0.75)
  e <- if (index > 2) pmax(0, round(t - runif(n) * 12, 1)) * rbinom(n, 1, 0.5) else NULL
  if (!is.null(e)) {
    keep <- e < t
    g <- g[keep]; t <- t[keep]; s <- s[keep]; e <- e[keep]
  }
  case(sprintf("random %d", index), g, t, s, entry = e, landmarks = c(1, 5, 10.25),
       grid = c(2, 4, 8, 16, 32))
}

entries <- character()
for (c in cases) {
  y <- if (is.null(c$entry)) Surv(c$time, c$status) else Surv(c$entry, c$time, c$status)
  g <- factor(c$group)
  strat <- if (is.null(c$stratum)) rep(1, length(c$time)) else c$stratum
  curves <- character()
  for (level in levels(g)) {
    at <- g == level
    fit <- survfit(y[at] ~ 1, conf.type = "log-log", conf.int = c$level)
    km <- cbind(fit$time, fit$n.risk, fit$n.event, fit$n.censor, fit$surv,
                bound1(fit$lower, fit$surv), bound1(fit$upper, fit$surv))
    q <- quantile(fit, 0.5)
    last <- max(fit$time)
    marks <- c$landmarks[c$landmarks <= last]
    landmark <- if (length(marks)) {
      s <- summary(fit, times = marks)
      cbind(s$time, s$surv, bound1(s$lower, s$surv), bound1(s$upper, s$surv))
    } else matrix(numeric(), 0, 4)
    grid <- matrix(numeric(), 0, 7)
    previous <- -Inf
    entry <- if (is.null(c$entry)) rep(-Inf, sum(at)) else c$entry[at]
    for (point in c$grid) {
      inside <- c$time[at] > previous & c$time[at] <= point
      risk <- sum(entry < point & point <= c$time[at])
      row <- c(point, risk, sum(inside & c$status[at] == 1), sum(inside & c$status[at] == 0))
      if (point <= last) {
        s <- summary(fit, times = point)
        row <- c(row, s$surv, bound1(s$lower, s$surv), bound1(s$upper, s$surv))
      } else row <- c(row, NA, NA, NA)
      grid <- rbind(grid, row)
      previous <- point
    }
    curves <- c(curves, obj(km = rows(km), median = vec(c(q$quantile, q$lower, q$upper)),
                            landmarks = rows(landmark), grid = rows(grid)))
  }
  if (is.null(c$entry)) {
    d <- if (is.null(c$stratum)) survdiff(y ~ g) else survdiff(y ~ g + strata(strat))
    df <- sum(if (is.matrix(d$exp)) rowSums(d$exp) > 0 else d$exp > 0) - 1
    logrank <- obj(statistic = num(d$chisq), df = num(df), p = num(1 - pchisq(d$chisq, df)))
  } else {
    f <- suppressWarnings(if (is.null(c$stratum)) coxph(y ~ g, ties = "exact") else
      coxph(y ~ g + strata(strat), ties = "exact"))
    df <- nlevels(g) - 1
    logrank <- obj(statistic = num(f$score), df = num(df),
                   p = num(pchisq(f$score, df, lower.tail = FALSE)))
  }
  events <- tapply(c$status, g, sum)
  kept <- g %in% names(events)[events > 0]
  gk <- droplevels(g[kept])
  yk <- y[kept]
  sk <- strat[kept]
  converged <- TRUE
  control <- coxph.control(iter.max = c$iter_max)
  fit <- withCallingHandlers(
    if (is.null(c$stratum)) coxph(yk ~ gk, ties = "efron", control = control) else
      coxph(yk ~ gk + strata(sk), ties = "efron", control = control),
    warning = function(w) {
      if (grepl("Ran out of iterations", conditionMessage(w))) converged <<- FALSE
      invokeRestart("muffleWarning")
    })
  var <- fit$var
  coef <- unname(fit$coefficients)
  infs <- abs(colSums(as.matrix(residuals(fit, type = "score"))) %*% var)
  infinite <- converged & (infs > control$eps) & (infs > control$toler.inf * abs(coef))
  zph <- tryCatch(suppressWarnings(cox.zph(fit, transform = "km"))$table, error = function(e) NULL)
  entries <- c(entries, obj(
    name = sprintf("\"%s\"", c$name),
    group = vec(c$group), time = vec(c$time), status = vec(c$status),
    entry = if (is.null(c$entry)) "null" else vec(c$entry),
    stratum = if (is.null(c$stratum)) "null" else vec(c$stratum),
    level = num(c$level), landmarks = vec(c$landmarks), grid = vec(c$grid),
    iter_max = num(c$iter_max),
    curves = paste0("[", paste(curves, collapse = ", "), "]"),
    logrank = logrank,
    cox = obj(groups = vec(as.numeric(levels(gk))), coef = vec(coef),
              se = vec(sqrt(diag(var))), loglik = vec(fit$loglik), iter = num(fit$iter),
              converged = if (converged) "true" else "false",
              infinite = paste0("[", paste(ifelse(infinite, "true", "false"), collapse = ", "), "]")),
    zph = if (is.null(zph)) "null" else obj(statistic = num(zph[nrow(zph), "chisq"]),
                                            df = num(zph[nrow(zph), "df"]),
                                            p = num(zph[nrow(zph), "p"]))
  ))
}

cat(sprintf("{\"r\": \"%s\", \"survival\": \"%s\",\n\"cases\": [%s]}\n", R.version.string,
            as.character(packageVersion("survival")), paste(entries, collapse = ",\n  ")))
