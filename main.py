import requests
from bs4 import BeautifulSoup
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional
import re
import json

HEADERS =  {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_subreddit_page_url(subreddit: str):
    return f"https://old.reddit.com/r/{subreddit}"

def get_post_page_url(subreddit: str, id: str):
    return f"https://old.reddit.com/r/{subreddit}/comments/{id}/"

@dataclass
class PostItem:
    id: str
    title: str

@dataclass
class Author:
    username: str
    id: Optional[str] = None  

@dataclass
class Comment:
    id: str
    author: Author
    content: str
    points: int
    create_date: Optional[datetime]
    parent_id: Optional[str] = None  

@dataclass
class PostDetails:
    id: str
    title: str
    content: str
    author: Author         
    subreddit: str         
    comments: List[Comment]
    comments_qty: int
    points: int
    create_date: Optional[datetime]


# --- HELPER FUNCTIONS ---

def datetime_serializer(obj):
    """
    Custom serializer to handle datetime objects when saving to JSON.
    Converts datetime to an ISO format string.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} is not JSON serializable")

def export_posts_to_json(posts_list: List['PostDetails'], file_path: str):
    """
    Takes a list of PostDetails dataclasses, nests the comments hierarchically,
    and saves them to a formatted JSON file.
    """
    # 1. Convert the list of Dataclasses into a list of standard dictionaries
    posts_as_dicts = [asdict(post) for post in posts_list]
    
    # 2. Process each post to build the comment tree
    for post_data in posts_as_dicts:
        flat_comments_list = post_data.get('comments', [])
        
        # Initialize a 'replies' list for every comment and build a lookup map
        # This allows O(1) access to any comment by its ID
        comment_lookup_map = {}
        for single_comment in flat_comments_list:
            single_comment['replies'] = []
            comment_lookup_map[single_comment['id']] = single_comment
            
        hierarchical_comments_tree = []
        
        # Iterate again to place each comment inside its parent's 'replies' list
        for single_comment in flat_comments_list:
            parent_id = single_comment.get('parent_id')
            
            # If the parent_id starts with 't1_', it's a reply to another comment
            if parent_id and str(parent_id).startswith('t1_'):
                parent_comment_node = comment_lookup_map.get(parent_id)
                
                if parent_comment_node:
                    # Attach this comment to its parent's replies
                    parent_comment_node['replies'].append(single_comment)
                else:
                    # Fallback: if the parent comment is missing from our scrape 
                    # (e.g., hidden under a "load more" button), we append it to the root
                    hierarchical_comments_tree.append(single_comment)
            else:
                # It's a top-level comment (parent_id starts with 't3_' or is None)
                hierarchical_comments_tree.append(single_comment)
                
        # 3. Replace the flat list with the newly structured tree
        post_data['comments'] = hierarchical_comments_tree

    # 4. Wrap everything in the final structure
    final_output_dict = {
        "posts": posts_as_dicts,
        "qty": len(posts_as_dicts)
    }    
    
    try:
        # Using encoding='utf-8' and ensure_ascii=False ensures emojis and special chars are saved correctly
        with open(file_path, 'w', encoding='utf-8') as output_file:
            json.dump(
                final_output_dict, 
                output_file, 
                indent=4,                     
                default=datetime_serializer,   
                ensure_ascii=False
            )
        print(f"Successfully saved {len(posts_list)} posts with nested comments to '{file_path}'.")
        
    except IOError as file_error:
        print(f"An error occurred while saving the file: {file_error}")


def extract_integer(text: str) -> int:
    """Extracts the first integer found in a string (e.g., '14 points' -> 14)."""
    if not text:
        return 0
    # Remove commas for numbers > 999
    clean_text = text.replace(',', '')
    matches = re.findall(r'\d+', clean_text)
    return int(matches[0]) if matches else 0

def parse_reddit_datetime(time_tag) -> Optional[datetime]:
    """Parses Reddit's ISO datetime attribute."""
    if time_tag and time_tag.has_attr('datetime'):
        try:
            return datetime.fromisoformat(time_tag['datetime'])
        except ValueError:
            return None
    return None
        

# --- MAIN SCRAPING FUNCTIONS ---

def scrape_subreddit_posts(target_subreddit, max_pages=3) -> Optional[List[PostItem]]:
    # Setup custom headers to avoid immediate blocks from Reddit
    custom_headers = HEADERS
    
    current_url = get_subreddit_page_url(target_subreddit)

    posts = []
    page_counter = 0

    while page_counter < max_pages and current_url:
        page_counter += 1
        print(f"\n--- Scraping Page {page_counter} | URL: {current_url} ---")
        
        try:
            # Execute the HTTP GET request
            html_response = requests.get(current_url, headers=custom_headers)
            html_response.raise_for_status()
            
            # Parse the raw HTML content
            parsed_soup = BeautifulSoup(html_response.text, 'html.parser')
            
            # Find all structural containers for posts
            post_elements = parsed_soup.find_all('div', class_='thing')
            
            # Extract specific data from each container
            for single_post in post_elements:
                post_title_element = single_post.find('a', class_='title')
                post_id = str(single_post['id']).split('_', 1)[1]
                
                if post_title_element:
                    extracted_title = post_title_element.text

                    posts.append(PostItem(post_id, extracted_title))
                    print(f"Id: {post_id}, Title: {extracted_title}")
            
            # --- PAGINATION LOGIC ---
            # Look for the span containing the 'Next' button
            next_button_span = parsed_soup.find('span', class_='next-button')
            
            if next_button_span:
                # Find the anchor tag and extract the href attribute
                next_link_element = next_button_span.find('a')
                if next_link_element and 'href' in next_link_element.attrs:
                    current_url = next_link_element['href']
                else:
                    print("\nReached the last page or next link not found.")
                    current_url = None # This stops the while loop
            else:
                print("\nNo 'next-button' span found. Stopping pagination.")
                current_url = None # This stops the while loop
                
            # Pause execution to respect server load (CRITICAL when paginating)
            if current_url:
                print("Sleeping for 3 seconds before fetching the next page...")
                time.sleep(3)

            return posts
            
        except requests.exceptions.RequestException as network_error:
            print(f"A network error occurred: {network_error}")
            break


def scrape_post_details(post_id: str) -> Optional[PostDetails]:
    # old.reddit automatically redirects /comments/{id} to the full URL
    target_url = f"https://old.reddit.com/comments/{post_id.split("_")[1]}/"
    
    headers = HEADERS

    try:
        response = requests.get(target_url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # --- EXTRACT MAIN POST DATA ---
        
        # The main post container is the first 'div.thing' inside 'div#siteTable'
        site_table = soup.find('div', id='siteTable')
        if not site_table:
            print("Could not find the main post container.")
            return None
            
        main_post_node = site_table.find('div', class_='thing')
        
        # Title
        title_element = main_post_node.find('a', class_='title')
        post_title = title_element.text if title_element else "Unknown Title"

        # Content (Self-text)
        body_element = main_post_node.find('div', class_='usertext-body')
        post_content = body_element.text.strip() if body_element else ""

        # Author and Subreddit
        author_element = main_post_node.find('a', class_='author')
        post_author_name = author_element.text if author_element else "[deleted]"
        
        subreddit_element = main_post_node.find('a', class_='subreddit')
        post_subreddit = subreddit_element.text if subreddit_element else "Unknown"

        # Points (Score)
        score_element = main_post_node.find('div', class_='score unvoted')
        post_points = extract_integer(score_element.text if score_element else "0")

        # Create Date
        time_element = main_post_node.find('time')
        post_date = parse_reddit_datetime(time_element)

        # Comments Quantity
        comments_link = soup.find('a', class_='bylink comments')
        post_comments_qty = extract_integer(comments_link.text if comments_link else "0")


        # --- EXTRACT COMMENTS ---
        
        extracted_comments = []
        comment_area = soup.find('div', class_='commentarea')
        
        if comment_area:
            # Find all individual comments in the thread
            comment_nodes = comment_area.find_all('div', class_='comment')
            
            for comment_node in comment_nodes:
                # Avoid extracting 'more comments' placeholder links
                if 'morechildren' in comment_node.get('class', []):
                    continue

                # ID
                comment_id = comment_node.get('data-fullname', 'unknown')

                # Author
                c_author_tag = comment_node.find('a', class_='author')
                c_author_name = c_author_tag.text if c_author_tag else "[deleted]"

                # Content
                c_body_tag = comment_node.find('div', class_='usertext-body')
                c_content = c_body_tag.text.strip() if c_body_tag else ""

                # Points
                c_score_tag = comment_node.find('span', class_='score unvoted')
                # Sometimes scores are hidden on new comments
                c_score_text = c_score_tag.text if c_score_tag and "score hidden" not in c_score_tag.text else "0"
                c_points = extract_integer(c_score_text)

                # Date
                c_time_tag = comment_node.find('time')
                c_date = parse_reddit_datetime(c_time_tag)

                # Parent ID Logic (To know which comment this replies to)
                parent_node = comment_node.find_parent('div', class_='comment')
                if parent_node:
                    c_parent_id = parent_node.get('data-fullname')
                else:
                    # If there's no parent comment, it's a top-level reply to the main post
                    c_parent_id = f"t3_{post_id}"

                # Append to list
                extracted_comments.append(Comment(
                    id=comment_id,
                    author=Author(username=c_author_name),
                    content=c_content,
                    points=c_points,
                    create_date=c_date,
                    parent_id=c_parent_id
                ))

        # --- ASSEMBLE FINAL OBJECT ---
        return PostDetails(
            id=post_id,
            title=post_title,
            content=post_content,
            author=Author(username=post_author_name),
            subreddit=post_subreddit,
            comments=extracted_comments,
            comments_qty=post_comments_qty,
            points=post_points,
            create_date=post_date
        )

    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch post: {e}")
        return None


def print_post_details(post: PostDetails):
    print("\n--- POST ---")
    print(f"Title: {post.title}")
    print(f"Author: {post.author.username}")
    print(f"Subreddit: {post.subreddit}")
    print(f"Post Date: {post.create_date}")
    print(f"Post Points: {post.points}")
    print(f"Total comments text stated: {post.comments_qty}")
    print(f"Actually extracted comments: {len(post.comments)}")
    print("\n--- Comments ---")
    for c in post.comments:
        print(f"- {c.author.username} ({c.points} pts): {c.content[:60]}... [Replies to: {c.parent_id}]")


def start():
    POSTS = []
    SUBREDDIT_NAME = "reddit.com"
    post_items = scrape_subreddit_posts(SUBREDDIT_NAME, 1)

    if post_items: 
        for item in post_items: 
            post_details = scrape_post_details(post_id=item.id)

            if post_details: 
                POSTS.append(post_details)
                print_post_details(post_details)
    else: 
        raise RuntimeError("No post items found")
 
    if POSTS:
        export_posts_to_json(POSTS, "posts/dump.json")

            
if __name__ == "__main__":
    start()