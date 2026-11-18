#import <Foundation/Foundation.h>

/* Images keyed by the game's resource ID, read from the player's own OBB. */
NSDictionary<NSString *, id> *pvz2_cursor_art(NSString *path);

/* The image bounds and anchor stay in the animation's authored coordinates. */
#import <CoreGraphics/CoreGraphics.h>
@interface PVZ2CursorPose : NSObject
@property(nonatomic, strong) id image;
@property(nonatomic) CGRect bounds;
@property(nonatomic) CGPoint anchor;
@end
PVZ2CursorPose *pvz2_cursor_plant_art(NSString *path, NSString *plant);
NSArray<NSString *> *pvz2_cursor_plant_names(NSString *path);
