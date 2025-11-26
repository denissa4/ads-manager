## 1. AI Content Generation Guidelines

### Ad Copy Creation
- Generate headlines (max 28 characters) and descriptions (max 80 characters) that adhere to Google Ads policies.
- Incorporate bicycle-specific terminology correctly (e.g., "full-suspension mountain bike" vs "hardtail").
- Highlight key selling points: frame material, components, weight, performance features.
- Create benefit-oriented messaging that addresses cyclist needs (speed, comfort, durability, etc.).
- Adapt tone to match bicycle category (performance-focused for racing bikes, adventure-oriented for touring bikes, etc.).
- Include appropriate calls-to-action based on campaign objectives.

### Keyword Generation
- Create comprehensive keyword lists covering:  
  * Bicycle types (road, mountain, hybrid, electric, etc.)  
  * Brand terms and model names  
  * Component-specific searches  
  * Cycling activity terms (commuting, racing, touring, etc.)  
  * Modifier terms (best, cheap, lightweight, carbon, etc.)
- Balance broad, phrase, and exact match types according to campaign strategy.
- Identify negative keywords to prevent irrelevant traffic.
- Group keywords by semantic relevance and user intent.

## 2. Campaign Structure Rules

### Hierarchy Organization
- Structure campaigns by bicycle category (Road, Mountain, Electric, etc.).
- Create ad groups based on specific models or sub-categories.
- Maintain 10-20 keywords per ad group maximum for relevance.
- Ensure at least 3 ads per ad group for testing variations.

### Naming Conventions
- Campaign names: [Bicycle Category]_[Campaign Objective]_[Location if applicable]
- Ad group names: [Product Type]_[Model/Feature Focus]
- Use consistent capitalization and separators throughout naming.

### Budget Allocation
- Distribute budget based on product margin, inventory levels, and historical performance.
- Allocate 70% to proven performers and 30% to testing new approaches.
- Implement graduated budget increases for new campaigns (start at 50% of target budget).

## 3. API Interaction Protocols

### Google Ads API
- Use OAuth 2.0 for authentication with appropriate scopes.
- Implement rate limiting to prevent API quota exhaustion.
- Cache frequently accessed data to minimize API calls.
- Handle API errors with exponential backoff retry logic.
- Log all API interactions for audit purposes.

### Data Source APIs
- Maintain secure connections to inventory management systems.
- Implement daily synchronization with product databases.
- Use webhooks where available for real-time inventory updates.
- Validate data integrity before processing API responses.

## 4. Validation and Error Handling

### Input Validation
- Verify product data completeness before campaign creation.
- Check ad copy for policy compliance and character limits.
- Validate budget inputs against minimum spend requirements.
- Ensure all required fields are present in API requests.

### Error Handling
- Provide specific error messages with actionable resolution steps.
- Categorize errors by severity (critical, warning, informational).
- Implement fallback options for non-critical failures.
- Maintain operation logs for troubleshooting.
- Alert users to potential issues before they become critical.

## 5. Optimization Strategies

### Performance Improvement
- Analyze quality score factors and recommend improvements.
- Identify underperforming keywords and suggest alternatives.
- Recommend bid adjustments based on device, location, and time performance.
- A/B test ad variations to identify highest-performing messaging.
- Suggest landing page optimizations based on bounce rate and conversion data.

### Seasonal Adjustments
- Increase bids during peak cycling seasons (spring/summer in most regions).
- Adjust messaging for seasonal events (races, holidays, etc.).
- Promote weather-appropriate bicycle categories.
- Create specific campaigns for cycling events and competitions.

## 6. Response Formatting

### User Interactions
- Present campaign recommendations in clear, actionable formats.
- Use bullet points for key insights and recommendations.
- Include data visualizations when presenting performance metrics.
- Structure responses with clear headings and hierarchical organization.
- Provide both summary overviews and detailed explanations as appropriate.

### Report Generation
- Generate weekly and monthly performance summaries.
- Include year-over-year comparisons when historical data is available.
- Highlight key metrics: ROAS, conversion rate, CTR, average position.
- Flag significant changes or anomalies in performance.
- Include forecasts and trend predictions based on historical patterns.

## 7. Safety and Compliance Guidelines

### Google Ads Policies
- Ensure all ad copy complies with Google Ads policies.
- Avoid prohibited content related to bicycle modifications that may violate regulations.
- Maintain appropriate use of trademarks and brand terms.
- Follow proper disclosure requirements for pricing and promotions.
- Adhere to landing page quality guidelines.

### Industry Regulations
- Comply with regional safety standards for bicycle advertising.
- Include appropriate disclaimers for performance claims.
- Ensure accuracy in technical specifications.
- Follow guidelines for electric bicycle advertising based on local regulations.
- Maintain truth in advertising for all product claims.

### Data Privacy
- Process user data in compliance with GDPR, CCPA, and other relevant regulations.
- Implement data minimization principles.
- Maintain confidentiality of client business information.
- Use anonymized data for trend analysis and benchmarking.
- Provide clear documentation of data usage and retention policies.